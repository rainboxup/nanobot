import json
from pathlib import Path

import pytest

from nanobot.workflow.service import (
    REASON_WORKFLOW_ACTION_UNSUPPORTED,
    REASON_WORKFLOW_DEFINITION_INVALID,
    REASON_WORKFLOW_DISABLED,
    REASON_WORKFLOW_NOT_FOUND,
    WorkflowService,
    WorkflowServiceError,
)
from nanobot.workflow.types import (
    WorkflowAction,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTrigger,
)


def _manual_workflow(*, workflow_id: str = "wf-demo", enabled: bool = True) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=workflow_id,
        name="Demo Workflow",
        version=1,
        enabled=enabled,
        trigger=WorkflowTrigger(kind="manual"),
        steps=[
            WorkflowStep(
                id="step-1",
                action=WorkflowAction(kind="message.send", params={"message": "hello"}),
            )
        ],
    )


def test_create_and_list_workflows_roundtrip(tmp_path: Path) -> None:
    store_path = tmp_path / ".nanobot-workflows.json"
    service = WorkflowService(store_path)
    created = service.create_workflow(_manual_workflow())
    assert created.id == "wf-demo"

    listed = service.list_workflows()
    assert [item.id for item in listed] == ["wf-demo"]
    assert listed[0].steps[0].action.kind == "message.send"

    reloaded = WorkflowService(store_path)
    listed_reloaded = reloaded.list_workflows()
    assert [item.id for item in listed_reloaded] == ["wf-demo"]
    assert listed_reloaded[0].name == "Demo Workflow"


def test_create_workflow_rejects_invalid_definition(tmp_path: Path) -> None:
    service = WorkflowService(tmp_path / ".nanobot-workflows.json")
    invalid = WorkflowDefinition(
        id="wf-invalid",
        name="Invalid",
        version=1,
        enabled=True,
        trigger=WorkflowTrigger(kind="manual"),
        steps=[],
    )
    with pytest.raises(WorkflowServiceError) as excinfo:
        service.create_workflow(invalid)
    assert excinfo.value.code == REASON_WORKFLOW_DEFINITION_INVALID


def test_update_workflow_roundtrip(tmp_path: Path) -> None:
    service = WorkflowService(tmp_path / ".nanobot-workflows.json")
    service.create_workflow(_manual_workflow(workflow_id="wf-update"))

    updated = WorkflowDefinition(
        id="wf-update",
        name="Updated Workflow",
        version=2,
        enabled=True,
        trigger=WorkflowTrigger(kind="manual"),
        steps=[
            WorkflowStep(
                id="step-1",
                action=WorkflowAction(kind="tool.call", params={"tool_name": "status"}),
            )
        ],
    )
    result = service.update_workflow(updated)

    assert result.name == "Updated Workflow"
    listed = service.list_workflows()
    assert listed[0].version == 2
    assert listed[0].steps[0].action.kind == "tool.call"


def test_delete_workflow_returns_boolean(tmp_path: Path) -> None:
    service = WorkflowService(tmp_path / ".nanobot-workflows.json")
    service.create_workflow(_manual_workflow(workflow_id="wf-delete"))

    assert service.delete_workflow("wf-delete") is True
    assert service.delete_workflow("wf-delete") is False
    assert service.list_workflows() == []


def test_run_workflow_executes_steps_in_deterministic_order(tmp_path: Path) -> None:
    executed: list[str] = []

    def _runner(workflow: WorkflowDefinition, step: WorkflowStep, run_id: str) -> None:
        assert workflow.id == "wf-order"
        assert run_id
        executed.append(step.id)

    service = WorkflowService(tmp_path / ".nanobot-workflows.json", action_runner=_runner)
    service.create_workflow(
        WorkflowDefinition(
            id="wf-order",
            name="Order",
            version=1,
            enabled=True,
            trigger=WorkflowTrigger(kind="manual"),
            steps=[
                WorkflowStep(
                    id="s1",
                    action=WorkflowAction(kind="message.send", params={"message": "one"}),
                ),
                WorkflowStep(
                    id="s2",
                    action=WorkflowAction(kind="message.send", params={"message": "two"}),
                    enabled=False,
                ),
                WorkflowStep(
                    id="s3",
                    action=WorkflowAction(kind="tool.call", params={"tool_name": "noop"}),
                ),
            ],
        )
    )

    result = service.run_workflow("wf-order")
    assert result.status == "ok"
    assert result.reason_code is None
    assert executed == ["s1", "s3"]


def test_run_workflow_returns_not_found_reason_code(tmp_path: Path) -> None:
    service = WorkflowService(tmp_path / ".nanobot-workflows.json")
    result = service.run_workflow("missing")
    assert result.status == "failed"
    assert result.reason_code == REASON_WORKFLOW_NOT_FOUND


def test_disabled_workflow_returns_skipped(tmp_path: Path) -> None:
    service = WorkflowService(tmp_path / ".nanobot-workflows.json")
    service.create_workflow(_manual_workflow(workflow_id="wf-disabled", enabled=False))

    result = service.run_workflow("wf-disabled")
    assert result.status == "skipped"
    assert result.reason_code == REASON_WORKFLOW_DISABLED


def test_run_workflow_returns_unsupported_action_reason_from_store_data(tmp_path: Path) -> None:
    service = WorkflowService(tmp_path / ".nanobot-workflows.json")
    service.create_workflow(_manual_workflow(workflow_id="wf-unsupported"))

    store_payload = {
        "version": 1,
        "workflows": [
            {
                "id": "wf-unsupported",
                "name": "Unsupported Action",
                "version": 1,
                "enabled": True,
                "trigger": {"kind": "manual", "cron": None},
                "steps": [
                    {
                        "id": "s1",
                        "enabled": True,
                        "action": {"kind": "integration.call", "params": {"connector": "crm"}},
                    }
                ],
            }
        ],
    }
    service.store_path.write_text(json.dumps(store_payload), encoding="utf-8")

    result = service.run_workflow("wf-unsupported")
    assert result.status == "failed"
    assert result.reason_code == REASON_WORKFLOW_ACTION_UNSUPPORTED
