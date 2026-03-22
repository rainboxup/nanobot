"""Workflow service for model persistence and deterministic execution."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from nanobot.cron.types import CronSchedule
from nanobot.workflow.types import (
    WorkflowAction,
    WorkflowDefinition,
    WorkflowRunResult,
    WorkflowStep,
    WorkflowTrigger,
)

REASON_WORKFLOW_NOT_FOUND = "workflow_not_found"
REASON_WORKFLOW_DEFINITION_INVALID = "workflow_definition_invalid"
REASON_WORKFLOW_ACTION_UNSUPPORTED = "workflow_action_unsupported"
REASON_WORKFLOW_DISABLED = "workflow_disabled"
REASON_WORKFLOW_EXECUTION_FAILED = "workflow_execution_failed"

_SUPPORTED_WORKFLOW_ACTIONS = {"message.send", "tool.call"}
_SUPPORTED_WORKFLOW_TRIGGER_KINDS = {"manual", "cron"}
_SUPPORTED_CRON_KINDS = {"at", "every", "cron"}


class WorkflowServiceError(ValueError):
    """Typed workflow service error with stable reason code."""

    def __init__(self, code: str, detail: str) -> None:
        normalized_code = str(code or "").strip() or REASON_WORKFLOW_EXECUTION_FAILED
        normalized_detail = str(detail or "").strip() or normalized_code
        super().__init__(normalized_detail)
        self.code = normalized_code
        self.detail = normalized_detail


class WorkflowStoreCorruptionError(RuntimeError):
    """Raised when workflow store cannot be parsed safely."""


@dataclass
class WorkflowStore:
    """Workflow store persisted on disk."""

    version: int = 1
    workflows: list[WorkflowDefinition] = field(default_factory=list)


class WorkflowService:
    """Workflow CRUD and deterministic execution service."""

    STATE_FILENAME = ".nanobot-workflows.json"

    def __init__(
        self,
        store_path: Path | None = None,
        *,
        action_runner: Callable[[WorkflowDefinition, WorkflowStep, str], None] | None = None,
    ) -> None:
        self.store_path = Path(store_path).expanduser() if store_path is not None else Path(self.STATE_FILENAME)
        self._lock = threading.RLock()
        self._store: WorkflowStore | None = None
        self._last_mtime: float = 0.0
        self._action_runner = action_runner

    def list_workflows(self) -> list[WorkflowDefinition]:
        """List persisted workflows."""
        with self._lock:
            store = self._load_store_locked()
            return [self._clone_definition(item) for item in store.workflows]

    def create_workflow(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        """Create a workflow definition."""
        normalized = self._normalize_definition(defn)
        with self._lock:
            store = self._load_store_locked()
            if any(item.id == normalized.id for item in store.workflows):
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    f"workflow id '{normalized.id}' already exists",
                )
            store.workflows.append(normalized)
            self._save_store_locked()
            return self._clone_definition(normalized)

    def update_workflow(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        """Update existing workflow definition."""
        normalized = self._normalize_definition(defn)
        with self._lock:
            store = self._load_store_locked()
            for idx, item in enumerate(store.workflows):
                if item.id == normalized.id:
                    store.workflows[idx] = normalized
                    self._save_store_locked()
                    return self._clone_definition(normalized)
            raise WorkflowServiceError(
                REASON_WORKFLOW_NOT_FOUND,
                f"workflow '{normalized.id}' not found",
            )

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow by id."""
        workflow_key = str(workflow_id or "").strip()
        if not workflow_key:
            return False
        with self._lock:
            store = self._load_store_locked()
            before = len(store.workflows)
            store.workflows = [item for item in store.workflows if item.id != workflow_key]
            if len(store.workflows) == before:
                return False
            self._save_store_locked()
            return True

    def run_workflow(self, workflow_id: str, *, force: bool = False) -> WorkflowRunResult:
        """Run workflow deterministically in step-list order."""
        workflow_key = str(workflow_id or "").strip()
        run_id = self._new_run_id()
        if not workflow_key:
            return WorkflowRunResult(
                workflow_id=workflow_key,
                run_id=run_id,
                status="failed",
                reason_code=REASON_WORKFLOW_NOT_FOUND,
            )

        with self._lock:
            store = self._load_store_locked()
            workflow = next((item for item in store.workflows if item.id == workflow_key), None)
            if workflow is None:
                return WorkflowRunResult(
                    workflow_id=workflow_key,
                    run_id=run_id,
                    status="failed",
                    reason_code=REASON_WORKFLOW_NOT_FOUND,
                )

            try:
                normalized = self._normalize_definition(workflow)
            except WorkflowServiceError:
                return WorkflowRunResult(
                    workflow_id=workflow_key,
                    run_id=run_id,
                    status="failed",
                    reason_code=REASON_WORKFLOW_DEFINITION_INVALID,
                )

            if not bool(normalized.enabled) and not bool(force):
                return WorkflowRunResult(
                    workflow_id=workflow_key,
                    run_id=run_id,
                    status="skipped",
                    reason_code=REASON_WORKFLOW_DISABLED,
                )

            try:
                self._run_workflow_steps(normalized, run_id)
            except WorkflowServiceError as exc:
                if exc.code == REASON_WORKFLOW_ACTION_UNSUPPORTED:
                    reason_code = REASON_WORKFLOW_ACTION_UNSUPPORTED
                elif exc.code == REASON_WORKFLOW_DEFINITION_INVALID:
                    reason_code = REASON_WORKFLOW_DEFINITION_INVALID
                else:
                    reason_code = REASON_WORKFLOW_EXECUTION_FAILED
                return WorkflowRunResult(
                    workflow_id=workflow_key,
                    run_id=run_id,
                    status="failed",
                    reason_code=reason_code,
                )
            except Exception:
                return WorkflowRunResult(
                    workflow_id=workflow_key,
                    run_id=run_id,
                    status="failed",
                    reason_code=REASON_WORKFLOW_EXECUTION_FAILED,
                )

            return WorkflowRunResult(
                workflow_id=workflow_key,
                run_id=run_id,
                status="ok",
                reason_code=None,
            )

    def _run_workflow_steps(self, workflow: WorkflowDefinition, run_id: str) -> None:
        for step in workflow.steps:
            if not bool(step.enabled):
                continue
            action_kind = str(step.action.kind or "").strip().lower()
            if action_kind not in _SUPPORTED_WORKFLOW_ACTIONS:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_ACTION_UNSUPPORTED,
                    f"unsupported workflow action '{action_kind}'",
                )
            self._execute_step(workflow, step, run_id)

    def _execute_step(self, workflow: WorkflowDefinition, step: WorkflowStep, run_id: str) -> None:
        if self._action_runner is not None:
            self._action_runner(self._clone_definition(workflow), self._clone_step(step), str(run_id))
            return

        action_kind = str(step.action.kind or "").strip().lower()
        params = dict(step.action.params or {})
        if action_kind == "message.send":
            message = str(params.get("message") or "").strip()
            if not message:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_EXECUTION_FAILED,
                    "message.send requires non-empty params.message",
                )
            return

        if action_kind == "tool.call":
            tool_name = str(params.get("tool_name") or "").strip()
            if not tool_name:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_EXECUTION_FAILED,
                    "tool.call requires non-empty params.tool_name",
                )
            return

        raise WorkflowServiceError(
            REASON_WORKFLOW_ACTION_UNSUPPORTED,
            f"unsupported workflow action '{action_kind}'",
        )

    def _normalize_definition(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        if not isinstance(defn, WorkflowDefinition):
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow definition type is invalid",
            )

        workflow_id = str(defn.id or "").strip()
        if not workflow_id:
            raise WorkflowServiceError(REASON_WORKFLOW_DEFINITION_INVALID, "workflow id is required")

        name = str(defn.name or "").strip()
        if not name:
            raise WorkflowServiceError(REASON_WORKFLOW_DEFINITION_INVALID, "workflow name is required")

        try:
            version = int(defn.version)
        except Exception as exc:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow version must be an integer",
            ) from exc
        if version < 1:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow version must be >= 1",
            )

        trigger = self._normalize_trigger(defn.trigger)
        steps = self._normalize_steps(defn.steps)
        if not steps:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow must define at least one step",
            )

        return WorkflowDefinition(
            id=workflow_id,
            name=name,
            version=version,
            enabled=bool(defn.enabled),
            trigger=trigger,
            steps=steps,
        )

    def _normalize_trigger(self, trigger: WorkflowTrigger) -> WorkflowTrigger:
        if not isinstance(trigger, WorkflowTrigger):
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow trigger is required",
            )

        kind = str(trigger.kind or "").strip().lower()
        if kind not in _SUPPORTED_WORKFLOW_TRIGGER_KINDS:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                f"workflow trigger kind '{kind}' is invalid",
            )

        cron = trigger.cron
        if kind == "manual":
            if cron is not None:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    "manual trigger cannot include cron schedule",
                )
            return WorkflowTrigger(kind="manual", cron=None)

        normalized_cron = self._normalize_cron_schedule(cron)
        return WorkflowTrigger(kind="cron", cron=normalized_cron)

    def _normalize_cron_schedule(self, schedule: CronSchedule | None) -> CronSchedule:
        if not isinstance(schedule, CronSchedule):
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "cron trigger requires a cron schedule",
            )

        kind = str(schedule.kind or "").strip().lower()
        if kind not in _SUPPORTED_CRON_KINDS:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                f"cron schedule kind '{kind}' is invalid",
            )
        if kind == "at":
            if schedule.at_ms is None:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    "cron schedule kind=at requires at_ms",
                )
            return CronSchedule(kind="at", at_ms=int(schedule.at_ms))
        if kind == "every":
            if schedule.every_ms is None:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    "cron schedule kind=every requires every_ms",
                )
            every_ms = int(schedule.every_ms)
            if every_ms <= 0:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    "cron schedule every_ms must be > 0",
                )
            return CronSchedule(kind="every", every_ms=every_ms)

        expr = str(schedule.expr or "").strip()
        if not expr:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "cron schedule kind=cron requires expr",
            )
        tz = str(schedule.tz or "").strip() or None
        return CronSchedule(kind="cron", expr=expr, tz=tz)

    def _normalize_steps(self, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        if not isinstance(steps, list):
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow steps must be a list",
            )

        normalized_steps: list[WorkflowStep] = []
        seen_ids: set[str] = set()
        for item in steps:
            if not isinstance(item, WorkflowStep):
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    "workflow step is invalid",
                )
            step_id = str(item.id or "").strip()
            if not step_id:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    "workflow step id is required",
                )
            if step_id in seen_ids:
                raise WorkflowServiceError(
                    REASON_WORKFLOW_DEFINITION_INVALID,
                    f"workflow step id '{step_id}' is duplicated",
                )
            seen_ids.add(step_id)
            action = self._normalize_action(item.action)
            normalized_steps.append(
                WorkflowStep(
                    id=step_id,
                    action=action,
                    enabled=bool(item.enabled),
                )
            )
        return normalized_steps

    def _normalize_action(self, action: WorkflowAction) -> WorkflowAction:
        if not isinstance(action, WorkflowAction):
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow action is required",
            )
        kind = str(action.kind or "").strip().lower()
        if not kind:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow action kind is required",
            )
        params = dict(action.params or {})
        try:
            normalized_params = json.loads(json.dumps(params, ensure_ascii=False))
        except Exception as exc:
            raise WorkflowServiceError(
                REASON_WORKFLOW_DEFINITION_INVALID,
                "workflow action params must be JSON-serializable",
            ) from exc
        return WorkflowAction(kind=kind, params=normalized_params)

    def _load_store_locked(self) -> WorkflowStore:
        if self._store is not None and self.store_path.exists():
            mtime = self.store_path.stat().st_mtime
            if mtime != self._last_mtime:
                self._store = None
        if self._store is not None:
            return self._store

        if not self.store_path.exists():
            self._store = WorkflowStore()
            return self._store

        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WorkflowStoreCorruptionError(
                f"Failed to load workflow store from '{self.store_path}': {exc}"
            ) from exc

        workflows: list[WorkflowDefinition] = []
        for raw in list(data.get("workflows") or []):
            if not isinstance(raw, dict):
                continue
            workflows.append(self._definition_from_dict(raw))
        self._store = WorkflowStore(
            version=int(data.get("version") or 1),
            workflows=workflows,
        )
        self._last_mtime = self.store_path.stat().st_mtime
        return self._store

    def _save_store_locked(self) -> None:
        if self._store is None:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": int(self._store.version),
            "workflows": [self._definition_to_dict(item) for item in self._store.workflows],
        }
        tmp_path = self.store_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.store_path)
        self._last_mtime = self.store_path.stat().st_mtime

    def _definition_to_dict(self, defn: WorkflowDefinition) -> dict[str, Any]:
        cron = None
        if isinstance(defn.trigger.cron, CronSchedule):
            cron = {
                "kind": defn.trigger.cron.kind,
                "at_ms": defn.trigger.cron.at_ms,
                "every_ms": defn.trigger.cron.every_ms,
                "expr": defn.trigger.cron.expr,
                "tz": defn.trigger.cron.tz,
            }
        return {
            "id": defn.id,
            "name": defn.name,
            "version": int(defn.version),
            "enabled": bool(defn.enabled),
            "trigger": {
                "kind": defn.trigger.kind,
                "cron": cron,
            },
            "steps": [
                {
                    "id": item.id,
                    "enabled": bool(item.enabled),
                    "action": {
                        "kind": item.action.kind,
                        "params": dict(item.action.params or {}),
                    },
                }
                for item in defn.steps
            ],
        }

    def _definition_from_dict(self, data: dict[str, Any]) -> WorkflowDefinition:
        trigger_payload = data.get("trigger")
        trigger_kind = "manual"
        cron = None
        if isinstance(trigger_payload, dict):
            trigger_kind = str(trigger_payload.get("kind") or "manual").strip().lower() or "manual"
            cron_payload = trigger_payload.get("cron")
            if isinstance(cron_payload, dict):
                cron = CronSchedule(
                    kind=str(cron_payload.get("kind") or "every").strip().lower() or "every",
                    at_ms=cron_payload.get("at_ms"),
                    every_ms=cron_payload.get("every_ms"),
                    expr=cron_payload.get("expr"),
                    tz=cron_payload.get("tz"),
                )
        steps: list[WorkflowStep] = []
        for raw_step in list(data.get("steps") or []):
            if not isinstance(raw_step, dict):
                continue
            raw_action = raw_step.get("action")
            action_kind = ""
            params: dict[str, Any] = {}
            if isinstance(raw_action, dict):
                action_kind = str(raw_action.get("kind") or "").strip().lower()
                params = dict(raw_action.get("params") or {})
            steps.append(
                WorkflowStep(
                    id=str(raw_step.get("id") or "").strip(),
                    enabled=bool(raw_step.get("enabled", True)),
                    action=WorkflowAction(kind=action_kind or "message.send", params=params),
                )
            )
        return WorkflowDefinition(
            id=str(data.get("id") or "").strip(),
            name=str(data.get("name") or "").strip(),
            version=int(data.get("version") or 1),
            enabled=bool(data.get("enabled", True)),
            trigger=WorkflowTrigger(kind=trigger_kind, cron=cron),
            steps=steps,
        )

    def _clone_definition(self, defn: WorkflowDefinition) -> WorkflowDefinition:
        return self._definition_from_dict(self._definition_to_dict(defn))

    def _clone_step(self, step: WorkflowStep) -> WorkflowStep:
        return WorkflowStep(
            id=str(step.id or ""),
            enabled=bool(step.enabled),
            action=WorkflowAction(
                kind=str(step.action.kind or ""),
                params=dict(step.action.params or {}),
            ),
        )

    def _new_run_id(self) -> str:
        return uuid.uuid4().hex[:12]
