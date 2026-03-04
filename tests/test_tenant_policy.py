from nanobot.tenants.policy import allowlist_match, resolve_exec_effective, resolve_web_effective


def test_allowlist_match_requires_scoped_identity_or_tenant() -> None:
    allowlist = {"tenant-a", "web:alice"}
    assert allowlist_match(allowlist, "tenant-a", ["web:bob"]) is True
    assert allowlist_match(allowlist, "tenant-b", ["web:alice"]) is True
    assert allowlist_match(allowlist, "tenant-b", ["alice"]) is False


def test_resolve_exec_effective_reason_codes() -> None:
    effective, reasons = resolve_exec_effective(
        system_enabled=False,
        system_allowlisted=False,
        tenant_enabled=False,
        tenant_has_allowlist=True,
        tenant_allowlisted=False,
        user_enabled=False,
    )
    assert effective is False
    assert reasons == [
        "system_disabled",
        "tenant_disabled",
        "tenant_allowlist",
        "user_disabled",
    ]


def test_resolve_web_effective_reason_codes() -> None:
    effective, reasons = resolve_web_effective(
        system_enabled=False,
        tenant_enabled=False,
        user_enabled=False,
    )
    assert effective is False
    assert reasons == ["system_disabled", "tenant_policy", "user_disabled"]
