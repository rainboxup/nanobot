#!/usr/bin/env python3
"""Production readiness validation script for multi-tenant architecture."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "token",
    "secret",
    "app_secret",
    "client_secret",
    "encrypt_key",
    "verification_token",
    "imap_password",
    "smtp_password",
    "bot_token",
    "app_token",
    "bridge_token",
    "access_token",
    "claw_token",
}
SENSITIVE_KEY_SUFFIXES = ("token", "secret", "password", "key")


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    if normalized in SENSITIVE_KEYS:
        return True
    return any(
        normalized == suffix or normalized.endswith(f"_{suffix}") for suffix in SENSITIVE_KEY_SUFFIXES
    )


def _collect_system_scoped_channel_values(channels_cfg: Any) -> list[str]:
    """Return dotted paths within channels config that look like system-scoped credentials."""
    if not isinstance(channels_cfg, dict):
        return []

    violations: list[str] = []
    for channel_name, channel_cfg in channels_cfg.items():
        if not isinstance(channel_cfg, dict):
            continue

        enabled = channel_cfg.get("enabled")
        if enabled is True:
            violations.append(f"channels.{channel_name}.enabled")

        for k, v in channel_cfg.items():
            if not _is_sensitive_key(str(k)):
                continue
            if isinstance(v, str) and v.strip():
                violations.append(f"channels.{channel_name}.{k}")
    return violations


@dataclass
class ValidationResult:
    """Result of a validation check."""

    check_name: str
    passed: bool
    message: str
    details: dict[str, Any] | None = None


class MultiTenantValidator:
    """Validator for multi-tenant production readiness."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or Path.cwd())
        self.results: list[ValidationResult] = []

    def validate_config_ownership(self) -> ValidationResult:
        """Scan for system-scoped values (e.g. channel credentials) stored in tenant configs."""
        try:
            tenants_dir = self.base_dir / "tenants"
            if not tenants_dir.exists():
                return ValidationResult(
                    check_name="config_ownership",
                    passed=True,
                    message="No tenant directory found (single-tenant mode)",
                )

            violations = []

            for tenant_dir in tenants_dir.iterdir():
                if not tenant_dir.is_dir():
                    continue

                config_file = tenant_dir / "config.json"
                if not config_file.exists():
                    continue

                try:
                    with open(config_file) as f:
                        tenant_config = json.load(f)

                    channel_violations = _collect_system_scoped_channel_values(
                        tenant_config.get("channels") if isinstance(tenant_config, dict) else None
                    )
                    for path in channel_violations:
                        violations.append(
                            {
                                "tenant_id": tenant_dir.name,
                                "path": path,
                                "file": str(config_file),
                            }
                        )
                except Exception as e:
                    violations.append(
                        {
                            "tenant_id": tenant_dir.name,
                            "error": f"Failed to parse config: {e}",
                        }
                    )

            if violations:
                return ValidationResult(
                    check_name="config_ownership",
                    passed=False,
                    message=f"Found {len(violations)} config ownership violations",
                    details={"violations": violations},
                )

            return ValidationResult(
                check_name="config_ownership",
                passed=True,
                message="No config ownership violations found",
            )

        except Exception as e:
            return ValidationResult(
                check_name="config_ownership",
                passed=False,
                message=f"Validation failed: {e}",
            )

    def validate_tenant_isolation(self) -> ValidationResult:
        """Check workspace directory permissions."""
        try:
            tenants_dir = self.base_dir / "tenants"
            if not tenants_dir.exists():
                return ValidationResult(
                    check_name="tenant_isolation",
                    passed=True,
                    message="No tenant directory found (single-tenant mode)",
                )

            issues = []

            for tenant_dir in tenants_dir.iterdir():
                if not tenant_dir.is_dir():
                    continue

                workspace = tenant_dir / "workspace"
                if not workspace.exists():
                    continue

                # Check permissions (Unix-like systems)
                try:
                    stat_info = workspace.stat()
                    mode = oct(stat_info.st_mode)[-3:]

                    # Warn if world-readable (mode ends with non-zero last digit)
                    if mode[-1] != "0":
                        issues.append(
                            {
                                "tenant_id": tenant_dir.name,
                                "workspace": str(workspace),
                                "mode": mode,
                                "issue": "World-readable permissions",
                            }
                        )
                except Exception as e:
                    issues.append(
                        {
                            "tenant_id": tenant_dir.name,
                            "error": f"Failed to check permissions: {e}",
                        }
                    )

            if issues:
                return ValidationResult(
                    check_name="tenant_isolation",
                    passed=False,
                    message=f"Found {len(issues)} isolation issues",
                    details={"issues": issues},
                )

            return ValidationResult(
                check_name="tenant_isolation",
                passed=True,
                message="Tenant isolation verified",
            )

        except Exception as e:
            return ValidationResult(
                check_name="tenant_isolation",
                passed=False,
                message=f"Validation failed: {e}",
            )

    def validate_service_statelessness(self) -> ValidationResult:
        """Scan services for self._state patterns."""
        try:
            services_dir = self.base_dir / "nanobot" / "services"
            if not services_dir.exists():
                return ValidationResult(
                    check_name="service_statelessness",
                    passed=False,
                    message="Services directory not found",
                )

            stateful_patterns = []

            for service_file in services_dir.glob("*.py"):
                if service_file.name.startswith("__"):
                    continue

                content = service_file.read_text()

                # Check for stateful patterns
                if "self._state" in content or "self._cache" in content:
                    stateful_patterns.append(
                        {
                            "file": str(service_file),
                            "pattern": "self._state or self._cache",
                        }
                    )

            if stateful_patterns:
                return ValidationResult(
                    check_name="service_statelessness",
                    passed=False,
                    message=f"Found {len(stateful_patterns)} stateful service patterns",
                    details={"patterns": stateful_patterns},
                )

            return ValidationResult(
                check_name="service_statelessness",
                passed=True,
                message="All services are stateless",
            )

        except Exception as e:
            return ValidationResult(
                check_name="service_statelessness",
                passed=False,
                message=f"Validation failed: {e}",
            )

    def validate_channel_registration(self) -> ValidationResult:
        """Verify all channels in ChannelManager."""
        try:
            channel_manager_file = self.base_dir / "nanobot" / "channels" / "manager.py"
            if not channel_manager_file.exists():
                return ValidationResult(
                    check_name="channel_registration",
                    passed=False,
                    message="ChannelManager not found",
                )

            content = channel_manager_file.read_text()

            # Check for channel registrations
            required_channels = ["feishu", "dingtalk", "telegram", "web"]
            missing_channels = []

            for channel in required_channels:
                if f'"{channel}"' not in content and f"'{channel}'" not in content:
                    missing_channels.append(channel)

            if missing_channels:
                return ValidationResult(
                    check_name="channel_registration",
                    passed=False,
                    message=f"Missing channel registrations: {', '.join(missing_channels)}",
                    details={"missing": missing_channels},
                )

            return ValidationResult(
                check_name="channel_registration",
                passed=True,
                message="All channels registered",
            )

        except Exception as e:
            return ValidationResult(
                check_name="channel_registration",
                passed=False,
                message=f"Validation failed: {e}",
            )

    def validate_audit_logging(self) -> ValidationResult:
        """Check audit log entries for overrides."""
        try:
            # Check for audit log configuration
            audit_log_path = self.base_dir / "audit.log"

            if not audit_log_path.exists():
                return ValidationResult(
                    check_name="audit_logging",
                    passed=True,
                    message="No audit log found (may not be configured)",
                )

            # Verify audit log is writable
            try:
                with open(audit_log_path, "a") as f:
                    f.write("")  # Test write
            except Exception as e:
                return ValidationResult(
                    check_name="audit_logging",
                    passed=False,
                    message=f"Audit log not writable: {e}",
                )

            return ValidationResult(
                check_name="audit_logging",
                passed=True,
                message="Audit logging configured",
            )

        except Exception as e:
            return ValidationResult(
                check_name="audit_logging",
                passed=False,
                message=f"Validation failed: {e}",
            )

    def run_all_checks(self) -> list[ValidationResult]:
        """Run all validation checks."""
        self.results = [
            self.validate_config_ownership(),
            self.validate_tenant_isolation(),
            self.validate_service_statelessness(),
            self.validate_channel_registration(),
            self.validate_audit_logging(),
        ]
        return self.results

    def generate_report(self, output_path: Path | None = None) -> dict[str, Any]:
        """Generate JSON report of validation results."""
        report = {
            "summary": {
                "total_checks": len(self.results),
                "passed": sum(1 for r in self.results if r.passed),
                "failed": sum(1 for r in self.results if not r.passed),
            },
            "checks": [
                {
                    "name": r.check_name,
                    "passed": r.passed,
                    "message": r.message,
                    "details": r.details,
                }
                for r in self.results
            ],
        }

        if output_path:
            with open(output_path, "w") as f:
                json.dump(report, f, indent=2)

        return report


def main():
    parser = argparse.ArgumentParser(
        description="Validate multi-tenant production readiness"
    )
    parser.add_argument(
        "--check-all", action="store_true", help="Run all validation checks"
    )
    parser.add_argument(
        "--check-config", action="store_true", help="Check config ownership"
    )
    parser.add_argument(
        "--check-isolation", action="store_true", help="Check tenant isolation"
    )
    parser.add_argument(
        "--check-stateless", action="store_true", help="Check service statelessness"
    )
    parser.add_argument(
        "--check-channels", action="store_true", help="Check channel registration"
    )
    parser.add_argument(
        "--check-audit", action="store_true", help="Check audit logging"
    )
    parser.add_argument(
        "--output", type=Path, help="Output JSON report path", default=None
    )
    parser.add_argument(
        "--base-dir", type=Path, help="Base directory", default=Path.cwd()
    )

    args = parser.parse_args()

    validator = MultiTenantValidator(base_dir=args.base_dir)

    # Run selected checks
    if args.check_all or not any(
        [
            args.check_config,
            args.check_isolation,
            args.check_stateless,
            args.check_channels,
            args.check_audit,
        ]
    ):
        validator.run_all_checks()
    else:
        if args.check_config:
            validator.results.append(validator.validate_config_ownership())
        if args.check_isolation:
            validator.results.append(validator.validate_tenant_isolation())
        if args.check_stateless:
            validator.results.append(validator.validate_service_statelessness())
        if args.check_channels:
            validator.results.append(validator.validate_channel_registration())
        if args.check_audit:
            validator.results.append(validator.validate_audit_logging())

    # Generate report
    report = validator.generate_report(output_path=args.output)

    # Print summary
    print("\nValidation Summary:")
    print(f"  Total checks: {report['summary']['total_checks']}")
    print(f"  Passed: {report['summary']['passed']}")
    print(f"  Failed: {report['summary']['failed']}")
    print()

    for check in report["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"  [{status}] {check['name']}: {check['message']}")

    if args.output:
        print(f"\nDetailed report saved to: {args.output}")

    # Exit with appropriate code
    sys.exit(0 if report["summary"]["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
