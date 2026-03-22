"""Workflow domain types for headless workflow execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from nanobot.cron.types import CronSchedule

WorkflowTriggerKind = Literal["manual", "cron"]
WorkflowActionKind = Literal["message.send", "tool.call"]
WorkflowRunStatus = Literal["ok", "failed", "skipped"]


@dataclass
class WorkflowTrigger:
    """Defines how a workflow is triggered."""

    kind: WorkflowTriggerKind = "manual"
    cron: CronSchedule | None = None


@dataclass
class WorkflowAction:
    """Single step action payload."""

    kind: WorkflowActionKind = "message.send"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStep:
    """Single workflow step."""

    id: str
    action: WorkflowAction
    enabled: bool = True


@dataclass
class WorkflowDefinition:
    """Persisted workflow definition."""

    id: str
    name: str
    version: int
    enabled: bool
    trigger: WorkflowTrigger
    steps: list[WorkflowStep] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowRunResult:
    """Result of a workflow run."""

    workflow_id: str
    run_id: str
    status: WorkflowRunStatus
    reason_code: str | None = None
