#!/usr/bin/env python3
"""Architecture fitness checks for nanobot."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

_NOQA_ARCH_RE = re.compile(r"#\s*noqa:\s*architecture\b", re.IGNORECASE)


class ViolationRule(NamedTuple):
    """Architecture violation rule."""

    pattern: str
    forbidden_imports: list[str]
    reason: str


RULES = [
    ViolationRule(
        pattern="nanobot/web/api/*.py",
        forbidden_imports=[
            "nanobot.tenants.store",
            "nanobot.agent.multi_tenant",
            "nanobot.agent.context",
        ],
        reason="Web API must use services layer",
    ),
    ViolationRule(
        pattern="nanobot/services/*.py",
        forbidden_imports=["nanobot.web", "nanobot.cli"],
        reason="Services must not depend on web/CLI",
    ),
    ViolationRule(
        pattern="nanobot/channels/*.py",
        forbidden_imports=["nanobot.web", "nanobot.cli"],
        reason="Channels must not depend on web/CLI",
    ),
]


class ImportRecord(NamedTuple):
    module: str
    lineno: int
    end_lineno: int


def _module_path_for_file(*, file_path: Path, project_root: Path) -> tuple[str, str]:
    """Return (module, package) names for a python file within project_root."""
    relative = file_path.relative_to(project_root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    module = ".".join(parts)
    package = module.rsplit(".", 1)[0] if relative.name != "__init__.py" and "." in module else module
    return module, package


def _resolve_import_from_module(*, current_package: str, module: str | None, level: int) -> str:
    """Resolve relative `from ... import ...` to an absolute module-like path."""
    if not level:
        return str(module or "")

    pkg_parts = [p for p in str(current_package or "").split(".") if p]
    if level > len(pkg_parts):
        prefix: list[str] = []
    else:
        prefix = pkg_parts[: len(pkg_parts) - (level - 1)]

    module_parts = [p for p in str(module or "").split(".") if p]
    return ".".join(prefix + module_parts)


class ImportVisitor(ast.NodeVisitor):
    """AST visitor to extract imports."""

    def __init__(self, *, current_package: str) -> None:
        self.current_package = current_package
        self.imports: list[ImportRecord] = []

    def _record(self, module: str, node: ast.AST) -> None:
        lineno = int(getattr(node, "lineno", 0) or 0)
        end_lineno = int(getattr(node, "end_lineno", lineno) or lineno)
        self.imports.append(ImportRecord(str(module or ""), lineno, end_lineno))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        resolved = _resolve_import_from_module(
            current_package=self.current_package,
            module=node.module,
            level=int(getattr(node, "level", 0) or 0),
        )
        if resolved:
            self._record(resolved, node)


def extract_imports(*, file_path: Path, project_root: Path) -> tuple[list[ImportRecord], list[str]]:
    """Extract all imports from a Python file.

    Raises on parse/read errors so CI fails closed.
    """
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    _module, package = _module_path_for_file(file_path=file_path, project_root=project_root)
    tree = ast.parse(content, filename=str(file_path))
    visitor = ImportVisitor(current_package=package)
    visitor.visit(tree)
    return visitor.imports, lines


def check_file(file_path: Path, project_root: Path, rule: ViolationRule) -> list[str]:
    """Check a file against a rule."""
    display_path = file_path.relative_to(project_root)
    try:
        imports, lines = extract_imports(file_path=file_path, project_root=project_root)
    except Exception as exc:
        return [f"{display_path}:0: failed to parse file for architecture check: {exc}"]
    violations = []

    for imp, lineno, end_lineno in imports:
        if lineno > 0:
            start = max(1, lineno)
            end = max(start, end_lineno)
            if end <= len(lines) and any(
                _NOQA_ARCH_RE.search(lines[i - 1]) for i in range(start, end + 1)
            ):
                continue
        for forbidden in rule.forbidden_imports:
            if imp == forbidden or imp.startswith(forbidden + "."):
                violations.append(f"{display_path}:{lineno}: {imp} - {rule.reason}")

    return violations


def main() -> int:
    """Run architecture checks."""
    project_root = Path(__file__).parent.parent
    all_violations = []

    print("Running architecture fitness checks...")
    print()

    for rule in RULES:
        rule_violations: list[str] = []
        files = sorted(project_root.glob(rule.pattern))
        if not files:
            continue

        print(f"Checking: {rule.pattern}")
        print(f"  Rule: {rule.reason}")

        for file_path in files:
            if file_path.name.startswith("__"):
                continue

            violations = check_file(file_path, project_root, rule)
            if violations:
                rule_violations.extend(violations)
                for violation in violations:
                    print(f"  [FAIL] {violation}")

        if not rule_violations:
            print("  [PASS] No violations found")
        else:
            all_violations.extend(rule_violations)

        print()

    if all_violations:
        print(f"[FAIL] Found {len(all_violations)} architecture violation(s)")
        return 1

    print("[PASS] All architecture checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
