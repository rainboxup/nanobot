"""Tool bridge for workspace integration connector runtime."""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.services.integration_runtime import IntegrationRuntimeError


class IntegrationTool(Tool):
    """Invoke workspace-configured integration connectors."""

    def __init__(self, runtime: Any):
        self._runtime = runtime

    def set_context(self, channel: str, chat_id: str) -> None:
        setter = getattr(self._runtime, "set_context", None)
        if callable(setter):
            setter(channel, chat_id)

    @property
    def name(self) -> str:
        return "integration"

    @property
    def description(self) -> str:
        return (
            "Invoke a workspace integration connector operation. "
            "Use this for CRM/order/ERP connector actions configured by tenant admins."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "connector": {
                    "type": "string",
                    "description": "Connector name from workspace.integrations.connectors",
                },
                "operation": {
                    "type": "string",
                    "description": "Connector operation identifier, e.g. sync_contacts",
                },
                "payload": {
                    "type": "object",
                    "description": "Operation payload object",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Optional idempotency key to support replay-safe writes",
                },
            },
            "required": ["connector", "operation"],
        }

    async def execute(
        self,
        connector: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        try:
            result = await self._runtime.invoke(
                connector=connector,
                operation=operation,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        except IntegrationRuntimeError as exc:
            reason_code = str(exc.reason_code or "").strip() or "connector_execution_failed"
            return f"Error [{reason_code}]: {str(exc)}"
        except Exception:
            return "Error [connector_execution_failed]: Connector invocation failed."
        return json.dumps(result, ensure_ascii=False)
