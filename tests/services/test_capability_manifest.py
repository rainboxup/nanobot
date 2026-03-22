from nanobot.config.schema import Config, PackagingProfile
from nanobot.services.capability_manifest import validate_packaging_profile
from nanobot.services.help_docs import HelpDocsRegistry


def test_validate_packaging_profile_ready_when_requirements_met() -> None:
    config = Config()
    config.packaging.active_profile = "enterprise"
    config.packaging.capabilities.integration_contract = True
    config.packaging.capabilities.workflow_core = True
    config.packaging.profiles["enterprise"] = PackagingProfile(
        name="enterprise",
        required_capabilities=["integration_contract", "workflow_core"],
        required_help_slugs=["config-ownership"],
    )

    payload = validate_packaging_profile(config, HelpDocsRegistry.default())

    assert payload["profile"] == "enterprise"
    assert payload["ready"] is True
    assert payload["missing_capabilities"] == []
    assert payload["missing_help_docs"] == []
    assert payload["reason_codes"] == []


def test_validate_packaging_profile_reports_missing_capabilities() -> None:
    config = Config()
    config.packaging.active_profile = "prod"
    config.packaging.capabilities.integration_contract = False
    config.packaging.profiles["prod"] = PackagingProfile(
        name="prod",
        required_capabilities=["integration_contract"],
        required_help_slugs=[],
    )

    payload = validate_packaging_profile(config, HelpDocsRegistry.default())

    assert payload["profile"] == "prod"
    assert payload["ready"] is False
    assert payload["missing_capabilities"] == ["integration_contract"]
    assert payload["missing_help_docs"] == []
    assert "packaging_missing_capabilities" in payload["reason_codes"]


def test_validate_packaging_profile_reports_missing_help_docs() -> None:
    config = Config()
    config.packaging.active_profile = "prod"
    config.packaging.capabilities.integration_contract = True
    config.packaging.profiles["prod"] = PackagingProfile(
        name="prod",
        required_capabilities=["integration_contract"],
        required_help_slugs=["missing-help-doc-slug"],
    )

    payload = validate_packaging_profile(config, HelpDocsRegistry.default())

    assert payload["profile"] == "prod"
    assert payload["ready"] is False
    assert payload["missing_capabilities"] == []
    assert payload["missing_help_docs"] == ["missing-help-doc-slug"]
    assert "packaging_missing_help_docs" in payload["reason_codes"]
