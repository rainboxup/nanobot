from nanobot.services.config_ownership import ConfigScope, OwnershipDecision
from nanobot.web.api.channels import _workspace_routing_ownership_error


def test_workspace_routing_ownership_error_treats_unreachable_permission_code_as_generic() -> None:
    decision = OwnershipDecision(
        allowed=False,
        scope=ConfigScope.WORKSPACE,
        reason_code="insufficient_permissions",
    )

    status_code, reason_code, reason = _workspace_routing_ownership_error(decision)

    assert status_code == 409
    assert reason_code == "insufficient_permissions"
    assert reason == "Workspace channel routing is unavailable."
