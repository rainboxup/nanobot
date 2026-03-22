"""Workspace integration runtime service and secure execution bridge."""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from nanobot.config.schema import WorkspaceIntegrationConfig
from nanobot.tenants.validation import validate_tenant_id, validate_workspace_integration_name

logger = logging.getLogger(__name__)

_OPERATION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_DEFAULT_TIMEOUT_S = 30
_MAX_TIMEOUT_S = 300

_CONNECTOR_REASON_SUMMARIES = {
    "connector_name_invalid": "Connector name is invalid.",
    "connector_not_configured": "Connector is not configured in workspace settings.",
    "connector_disabled": "Connector is disabled.",
    "connector_operation_invalid": "Connector operation is invalid.",
    "connector_payload_invalid": "Connector payload must be an object.",
    "connector_provider_missing": "Connector provider is missing.",
    "connector_provider_unavailable": "Connector provider adapter is unavailable.",
    "connector_tenant_context_invalid": "Tenant context is invalid for connector invocation.",
    "connector_tenant_boundary_violation": "Cross-tenant connector invocation is blocked.",
    "connector_execution_failed": "Connector invocation failed during execution.",
    "connector_response_invalid": "Connector adapter returned an invalid response.",
}


def explain_connector_failure_reason(reason_code: str | None) -> str | None:
    """Return a stable summary for connector runtime failure reason codes."""
    key = str(reason_code or "").strip()
    if not key:
        return None
    return _CONNECTOR_REASON_SUMMARIES.get(key)


def _extract_web_tenant_id(chat_id: str | None) -> str | None:
    text = str(chat_id or "").strip()
    if not text:
        return None
    parts = text.split(":", 2)
    if len(parts) != 3 or parts[0] != "web":
        return None
    candidate = str(parts[1] or "").strip()
    if not candidate:
        return None
    try:
        return validate_tenant_id(candidate)
    except ValueError:
        return None


class IntegrationRuntimeError(RuntimeError):
    """Raised when connector invocation fails with a stable reason code."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip() or "connector_execution_failed"
        self.details = details or {}


@dataclass(frozen=True)
class ConnectorInvocation:
    """Normalized connector invocation request passed to adapter implementations."""

    connector: str
    provider: str
    operation: str
    payload: dict[str, Any]
    metadata: dict[str, Any]
    timeout_s: int
    tenant_id: str | None
    idempotency_key: str | None = None


class IntegrationAdapterProtocol(Protocol):
    """Adapter contract for connector provider implementations."""

    async def execute(self, request: ConnectorInvocation) -> dict[str, Any] | str | None:
        ...


IntegrationAdapter = (
    IntegrationAdapterProtocol
    | Callable[[ConnectorInvocation], Awaitable[dict[str, Any] | str | None] | dict[str, Any] | str | None]
)
IntegrationAuditHook = Callable[..., None]


class IntegrationRuntimeService:
    """Tenant-aware integration runtime for connector execution."""

    def __init__(
        self,
        *,
        connectors: Mapping[str, WorkspaceIntegrationConfig | dict[str, Any]] | None = None,
        adapters: Mapping[str, IntegrationAdapter] | None = None,
        tenant_id: str | None = None,
        audit_hook: IntegrationAuditHook | None = None,
    ) -> None:
        self._connectors: dict[str, WorkspaceIntegrationConfig] = {}
        for raw_name, raw_config in dict(connectors or {}).items():
            normalized_name = validate_workspace_integration_name(str(raw_name))
            if isinstance(raw_config, WorkspaceIntegrationConfig):
                cfg = raw_config
            else:
                cfg = WorkspaceIntegrationConfig.model_validate(raw_config)
            self._connectors[normalized_name] = cfg

        self._adapters: dict[str, IntegrationAdapter] = {
            str(name or "").strip().lower(): adapter
            for name, adapter in dict(adapters or {}).items()
            if str(name or "").strip()
        }
        self._tenant_id = validate_tenant_id(str(tenant_id)) if str(tenant_id or "").strip() else None
        self._channel = ""
        self._chat_id = ""
        self._audit_hook = audit_hook

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set current runtime routing context for tenant-boundary checks."""
        self._channel = str(channel or "").strip().lower()
        self._chat_id = str(chat_id or "").strip()

    async def invoke(
        self,
        *,
        connector: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Invoke a configured connector through its provider adapter."""
        try:
            normalized_connector = self._normalize_connector_name(connector)
            normalized_operation = self._normalize_operation(operation)
            normalized_payload = self._normalize_payload(payload)
            cfg = self._resolve_connector_config(normalized_connector)
            context_tenant = self._resolve_context_tenant_id()
            runtime_tenant = self._tenant_id or context_tenant
            if self._tenant_id and context_tenant and context_tenant != self._tenant_id:
                raise IntegrationRuntimeError(
                    "connector_tenant_boundary_violation",
                    "Cross-tenant connector invocation is blocked.",
                    details={
                        "connector": normalized_connector,
                        "runtime_tenant_id": self._tenant_id,
                        "context_tenant_id": context_tenant,
                    },
                )

            provider = str(cfg.provider or "").strip().lower()
            if not provider:
                raise IntegrationRuntimeError(
                    "connector_provider_missing",
                    "Connector provider is required for invocation.",
                    details={"connector": normalized_connector},
                )

            adapter = self._adapters.get(provider)
            if adapter is None:
                raise IntegrationRuntimeError(
                    "connector_provider_unavailable",
                    f"Connector provider '{provider}' is not available in runtime.",
                    details={"connector": normalized_connector, "provider": provider},
                )

            timeout_s = min(_MAX_TIMEOUT_S, max(1, int(getattr(cfg, "timeout_s", _DEFAULT_TIMEOUT_S))))
            request = ConnectorInvocation(
                connector=normalized_connector,
                provider=provider,
                operation=normalized_operation,
                payload=normalized_payload,
                metadata=dict(getattr(cfg, "metadata", {}) or {}),
                timeout_s=timeout_s,
                tenant_id=runtime_tenant,
                idempotency_key=self._normalize_idempotency_key(idempotency_key),
            )

            raw_result = await self._run_adapter(adapter, request)
            normalized_result = self._normalize_adapter_result(raw_result)
        except IntegrationRuntimeError as exc:
            self._emit_audit(
                status="failed",
                tenant_id=self._tenant_id,
                connector=str(connector or "").strip().lower(),
                provider=None,
                operation=str(operation or "").strip(),
                reason_code=exc.reason_code,
                payload_keys=sorted((payload or {}).keys()) if isinstance(payload, dict) else [],
            )
            raise
        except Exception as exc:
            wrapped = IntegrationRuntimeError(
                "connector_execution_failed",
                "Connector invocation failed during adapter execution.",
                details={"error": str(exc)},
            )
            self._emit_audit(
                status="failed",
                tenant_id=self._tenant_id,
                connector=str(connector or "").strip().lower(),
                provider=None,
                operation=str(operation or "").strip(),
                reason_code=wrapped.reason_code,
                payload_keys=sorted((payload or {}).keys()) if isinstance(payload, dict) else [],
            )
            raise wrapped from exc

        response = {
            "status": "succeeded",
            "connector": request.connector,
            "provider": request.provider,
            "operation": request.operation,
            "tenant_id": request.tenant_id,
            "output": normalized_result,
        }
        self._emit_audit(
            status="succeeded",
            tenant_id=request.tenant_id,
            connector=request.connector,
            provider=request.provider,
            operation=request.operation,
            reason_code=None,
            payload_keys=sorted(request.payload.keys()),
        )
        return response

    @staticmethod
    def _normalize_connector_name(connector: str) -> str:
        raw = str(connector or "").strip()
        try:
            return validate_workspace_integration_name(raw)
        except ValueError as exc:
            raise IntegrationRuntimeError(
                "connector_name_invalid",
                "Connector name is invalid.",
                details={"connector": raw},
            ) from exc

    def _resolve_connector_config(self, connector: str) -> WorkspaceIntegrationConfig:
        cfg = self._connectors.get(connector)
        if cfg is None:
            raise IntegrationRuntimeError(
                "connector_not_configured",
                f"Connector '{connector}' is not configured.",
                details={"connector": connector},
            )
        if not bool(cfg.enabled):
            raise IntegrationRuntimeError(
                "connector_disabled",
                f"Connector '{connector}' is disabled.",
                details={"connector": connector},
            )
        return cfg

    @staticmethod
    def _normalize_operation(operation: str) -> str:
        normalized = str(operation or "").strip()
        if not normalized or not _OPERATION_RE.fullmatch(normalized):
            raise IntegrationRuntimeError(
                "connector_operation_invalid",
                "Operation must match [A-Za-z][A-Za-z0-9_.:-]{0,63}.",
                details={"operation": normalized},
            )
        return normalized

    @staticmethod
    def _normalize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise IntegrationRuntimeError(
                "connector_payload_invalid",
                "Payload must be a JSON object.",
            )
        return dict(payload)

    @staticmethod
    def _normalize_idempotency_key(value: str | None) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        return normalized[:128]

    def _resolve_context_tenant_id(self) -> str | None:
        if self._channel != "web":
            return self._tenant_id
        tenant_id = _extract_web_tenant_id(self._chat_id)
        if tenant_id is None:
            raise IntegrationRuntimeError(
                "connector_tenant_context_invalid",
                "Web connector invocation requires chat_id in web:<tenant_id>:<suffix> format.",
                details={"channel": self._channel, "chat_id": self._chat_id},
            )
        return tenant_id

    async def _run_adapter(
        self, adapter: IntegrationAdapter, request: ConnectorInvocation
    ) -> dict[str, Any] | str | None:
        execute = getattr(adapter, "execute", None)
        if callable(execute):
            maybe_result = execute(request)
        elif callable(adapter):
            maybe_result = adapter(request)
        else:
            raise IntegrationRuntimeError(
                "connector_provider_unavailable",
                f"Connector provider '{request.provider}' adapter is not callable.",
                details={"provider": request.provider},
            )

        if inspect.isawaitable(maybe_result):
            return await maybe_result
        return maybe_result

    @staticmethod
    def _normalize_adapter_result(raw_result: Any) -> dict[str, Any]:
        if raw_result is None:
            return {}
        if isinstance(raw_result, dict):
            return dict(raw_result)
        if isinstance(raw_result, str):
            return {"message": raw_result}
        if isinstance(raw_result, (int, float, bool)):
            return {"value": raw_result}
        if isinstance(raw_result, list):
            return {"items": list(raw_result)}
        raise IntegrationRuntimeError(
            "connector_response_invalid",
            "Connector adapter returned an unsupported response type.",
            details={"response_type": type(raw_result).__name__},
        )

    def _emit_audit(
        self,
        *,
        status: str,
        tenant_id: str | None,
        connector: str,
        provider: str | None,
        operation: str,
        reason_code: str | None,
        payload_keys: list[str],
    ) -> None:
        metadata: dict[str, Any] = {
            "connector": connector,
            "provider": provider,
            "operation": operation,
            "reason_code": reason_code,
            "reason_summary": explain_connector_failure_reason(reason_code),
            "payload_keys": list(payload_keys),
        }
        hook = self._audit_hook
        if hook is None:
            logger.info(
                "integration.connector.invoke status=%s tenant_id=%s connector=%s provider=%s operation=%s reason=%s",
                status,
                tenant_id,
                connector,
                provider,
                operation,
                reason_code,
            )
            return
        try:
            hook(
                event="integration.connector.invoke",
                status=status,
                tenant_id=tenant_id,
                metadata=metadata,
            )
        except Exception:
            logger.exception("Integration audit hook failed.")
