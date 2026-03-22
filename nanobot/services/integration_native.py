"""Native CRM/order/ERP integration adapters and sync status storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.services.integration_runtime import ConnectorInvocation, IntegrationRuntimeError

_STATUS_FILE_RELATIVE_PATH = Path(".nanobot") / "integrations" / "status.json"


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IntegrationStatusStore:
    """Persist latest per-connector sync result in workspace scope."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser()
        self.path = self.workspace / _STATUS_FILE_RELATIVE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"connectors": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"connectors": {}}
        if not isinstance(data, dict):
            return {"connectors": {}}
        connectors = data.get("connectors")
        if not isinstance(connectors, dict):
            return {"connectors": {}}
        return {"connectors": dict(connectors)}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def list_status(self) -> dict[str, dict[str, Any]]:
        payload = self._read()
        rows = payload.get("connectors")
        if not isinstance(rows, dict):
            return {}
        return {str(name): dict(value or {}) for name, value in rows.items() if isinstance(value, dict)}

    def get_status(self, connector: str) -> dict[str, Any] | None:
        rows = self.list_status()
        item = rows.get(str(connector or "").strip().lower())
        if not isinstance(item, dict):
            return None
        return dict(item)

    def record(
        self,
        *,
        connector: str,
        provider: str,
        domain: str,
        operation: str,
        status: str,
        reason_code: str | None,
        sync_id: str | None,
        synced_count: int | None,
    ) -> dict[str, Any]:
        normalized_connector = str(connector or "").strip().lower()
        entry = {
            "connector": normalized_connector,
            "provider": str(provider or "").strip().lower() or None,
            "domain": str(domain or "").strip().lower() or None,
            "operation": str(operation or "").strip() or None,
            "status": str(status or "").strip().lower() or "unknown",
            "reason_code": str(reason_code or "").strip() or None,
            "sync_id": str(sync_id or "").strip() or None,
            "synced_count": int(synced_count or 0),
            "updated_at": _utc_iso_now(),
        }
        payload = self._read()
        rows = payload.get("connectors")
        if not isinstance(rows, dict):
            rows = {}
        rows[normalized_connector] = entry
        payload["connectors"] = rows
        self._write(payload)
        return dict(entry)


def _resolve_sync_count(payload: dict[str, Any]) -> int:
    count_value = payload.get("count")
    if isinstance(count_value, int) and count_value > 0:
        return int(count_value)
    items = payload.get("items")
    if isinstance(items, list):
        return max(0, len(items))
    return 1


class _BaseNativeAdapter:
    def __init__(self, *, domain: str, allowed_operations: set[str], store: IntegrationStatusStore):
        self.domain = str(domain or "").strip().lower()
        self.allowed_operations = set(str(item).strip() for item in allowed_operations if str(item).strip())
        self.store = store

    async def execute(self, request: ConnectorInvocation) -> dict[str, Any]:
        operation = str(request.operation or "").strip()
        try:
            if operation not in self.allowed_operations:
                raise IntegrationRuntimeError(
                    "connector_operation_invalid",
                    f"Operation '{operation}' is not supported by provider '{request.provider}'.",
                    details={"operation": operation, "provider": request.provider},
                )
            if bool(request.payload.get("simulate_failure")):
                raise IntegrationRuntimeError(
                    "connector_execution_failed",
                    "Connector sync failed due to simulated upstream error.",
                    details={"operation": operation, "provider": request.provider},
                )

            synced_count = _resolve_sync_count(request.payload)
            sync_id = f"{self.domain}-{int(datetime.now(timezone.utc).timestamp())}"
            output = {
                "domain": self.domain,
                "operation": operation,
                "sync_id": sync_id,
                "synced_count": synced_count,
            }
            self.store.record(
                connector=request.connector,
                provider=request.provider,
                domain=self.domain,
                operation=operation,
                status="succeeded",
                reason_code=None,
                sync_id=sync_id,
                synced_count=synced_count,
            )
            return output
        except IntegrationRuntimeError as exc:
            self.store.record(
                connector=request.connector,
                provider=request.provider,
                domain=self.domain,
                operation=operation,
                status="failed",
                reason_code=exc.reason_code,
                sync_id=None,
                synced_count=0,
            )
            raise


class CRMNativeAdapter(_BaseNativeAdapter):
    def __init__(self, store: IntegrationStatusStore):
        super().__init__(
            domain="crm",
            allowed_operations={"sync_contacts", "sync_accounts"},
            store=store,
        )


class OrderNativeAdapter(_BaseNativeAdapter):
    def __init__(self, store: IntegrationStatusStore):
        super().__init__(
            domain="order",
            allowed_operations={"sync_orders", "sync_fulfillment"},
            store=store,
        )


class ERPNativeAdapter(_BaseNativeAdapter):
    def __init__(self, store: IntegrationStatusStore):
        super().__init__(
            domain="erp",
            allowed_operations={"sync_products", "sync_inventory"},
            store=store,
        )


def build_default_integration_adapters(*, workspace: Path) -> dict[str, Any]:
    """Build built-in native adapter mapping for integration runtime."""
    store = IntegrationStatusStore(workspace)
    crm = CRMNativeAdapter(store)
    order = OrderNativeAdapter(store)
    erp = ERPNativeAdapter(store)
    return {
        "crm_native": crm,
        "crm": crm,
        "order_native": order,
        "order": order,
        "erp_native": erp,
        "erp": erp,
    }
