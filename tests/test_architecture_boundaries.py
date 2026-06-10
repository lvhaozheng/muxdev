from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "muxdev"


def test_domain_layer_has_no_upper_layer_imports() -> None:
    assert _violations(
        SRC_ROOT / "domain",
        forbidden=(
            "muxdev.application",
            "muxdev.daemon",
            "muxdev.runtime",
            "muxdev.storage",
            "muxdev.providers",
            "muxdev.capabilities",
            "muxdev.plugins",
            "muxdev.presentation",
            "muxdev.api",
            "muxdev.cli",
            "muxdev.ui",
            "muxdev.services",
        ),
    ) == []


def test_runtime_never_imports_entry_surfaces() -> None:
    assert _violations(
        SRC_ROOT / "runtime",
        forbidden=("muxdev.cli", "muxdev.api", "muxdev.ui", "muxdev.presentation"),
    ) == []


def test_providers_never_import_daemon_or_entry_surfaces() -> None:
    assert _violations(
        SRC_ROOT / "providers",
        forbidden=("muxdev.daemon", "muxdev.api", "muxdev.cli", "muxdev.ui", "muxdev.presentation"),
    ) == []


def test_storage_never_imports_runtime_or_services() -> None:
    assert _violations(
        SRC_ROOT / "storage",
        forbidden=(
            "muxdev.application",
            "muxdev.daemon",
            "muxdev.runtime",
            "muxdev.services",
            "muxdev.api",
            "muxdev.cli",
            "muxdev.ui",
            "muxdev.presentation",
        ),
    ) == []


def test_api_does_not_import_supervisor_runtime() -> None:
    assert _violations(SRC_ROOT / "api", forbidden=("muxdev.runtime.supervisor",)) == []


def test_presentation_does_not_write_blackboard() -> None:
    presentation = SRC_ROOT / "presentation"
    if not presentation.exists():
        return
    imports = [
        (path.relative_to(REPO_ROOT).as_posix(), module)
        for path in presentation.rglob("*.py")
        for module in _imports(path)
        if module in {"muxdev.storage", "muxdev.storage.blackboard"} or module.startswith("muxdev.storage.blackboard.")
    ]
    assert imports == []


def test_cli_deep_imports_are_contained_to_legacy_modules() -> None:
    deep_roots = ("muxdev.runtime", "muxdev.storage")
    allowed = {
        "src/muxdev/cli/main.py",
    }
    actual = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (SRC_ROOT / "cli").rglob("*.py")
        if any(_is_forbidden(module, deep_roots) for module in _imports(path))
    }
    assert actual <= allowed


def _violations(root: Path, *, forbidden: tuple[str, ...]) -> list[tuple[str, str]]:
    if not root.exists():
        return []
    return [
        (path.relative_to(REPO_ROOT).as_posix(), module)
        for path in root.rglob("*.py")
        for module in _imports(path)
        if _is_forbidden(module, forbidden)
    ]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_for(path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(package, node)
            if module:
                modules.append(module)
    return modules


def _package_for(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT / "src").with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()
    return ".".join(parts)


def _resolve_import_from(package: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    package_parts = package.split(".")
    base = package_parts[: max(0, len(package_parts) - node.level + 1)]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base) if base else None


def _is_forbidden(module: str | None, forbidden: tuple[str, ...]) -> bool:
    if not module:
        return False
    return any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)
