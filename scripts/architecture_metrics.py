"""Emit deterministic architecture metrics for the muxdev source tree."""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "muxdev"
FOUR_LAYERS = {
    "Domain": {"core", "domain", "models"},
    "Application": {"application"},
    "Adapter": {"config", "providers", "services", "storage", "workflows"},
    "Composition": {"api", "cli", "runtime"},
}


def _python_files() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def _logical_lines(path: Path) -> int:
    return sum(
        1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _module(path: Path) -> str:
    relative = path.relative_to(SOURCE).with_suffix("")
    return ".".join(relative.parts)


def _layer(module: str) -> str:
    return module.split(".", 1)[0]


def _imports(tree: ast.AST, module: str) -> set[str]:
    result: set[str] = set()
    package = module.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("muxdev."):
                    result.add(alias.name.removeprefix("muxdev.").split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: max(0, len(package) - node.level + 1)]
                target = [*base, *((node.module or "").split("."))]
                if target and target[0]:
                    result.add(target[0])
            elif node.module and node.module.startswith("muxdev."):
                result.add(node.module.removeprefix("muxdev.").split(".", 1)[0])
    result.discard(_layer(module))
    return result


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return cyclic strongly connected components, not every possible cycle."""
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph.get(node, set())):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == node:
                break
        if len(component) > 1:
            components.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(components)


def _four_layer_boundaries(graph: dict[str, set[str]]) -> dict[str, object]:
    owner = {module: layer for layer, modules in FOUR_LAYERS.items() for module in modules}
    rank = {"Domain": 0, "Application": 1, "Adapter": 2, "Composition": 3}
    edges: dict[str, set[str]] = defaultdict(set)
    violations: list[str] = []
    for source, targets in graph.items():
        source_layer = owner.get(source)
        if not source_layer:
            continue
        for target in targets:
            target_layer = owner.get(target)
            if not target_layer or target_layer == source_layer:
                continue
            edges[source_layer].add(target_layer)
            if rank[target_layer] > rank[source_layer]:
                violations.append(f"{source}({source_layer}) -> {target}({target_layer})")
    return {
        "modules": {layer: sorted(modules) for layer, modules in FOUR_LAYERS.items()},
        "edges": {layer: sorted(edges.get(layer, set())) for layer in FOUR_LAYERS},
        "reverse_dependency_violations": sorted(violations),
    }


def collect() -> dict[str, object]:
    files = _python_files()
    file_lines = {path.relative_to(ROOT).as_posix(): _logical_lines(path) for path in files}
    graph: dict[str, set[str]] = defaultdict(set)
    long_functions: list[dict[str, object]] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        module = _module(path)
        graph[_layer(module)].update(_imports(tree, module))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                length = node.end_lineno - node.lineno + 1
                if length > 120:
                    long_functions.append(
                        {
                            "file": path.relative_to(ROOT).as_posix(),
                            "name": node.name,
                            "line": node.lineno,
                            "lines": length,
                        }
                    )

    all_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in files)
    workflow_data = yaml.safe_load((SOURCE / "config" / "defaults" / "workflows.yaml").read_text(encoding="utf-8"))
    workflows = workflow_data.get("workflows", {}) if isinstance(workflow_data, dict) else {}
    skill_root = SOURCE / "skills"
    skills = sorted(path.name for path in skill_root.glob("default-*") if (path / "SKILL.md").exists())
    return {
        "schema": "muxdev.architecture_metrics.v1",
        "source": {
            "python_files": len(files),
            "logical_lines": sum(file_lines.values()),
            "largest_files": [
                {"file": name, "lines": lines}
                for name, lines in sorted(file_lines.items(), key=lambda item: item[1], reverse=True)[:10]
            ],
            "functions_over_120_lines": sorted(long_functions, key=lambda item: int(item["lines"]), reverse=True),
        },
        "architecture": {
            "layer_edges": {name: sorted(targets) for name, targets in sorted(graph.items())},
            "layer_cycles": _cycles(graph),
            "four_layers": _four_layer_boundaries(graph),
        },
        "surfaces": {
            "http_routes": len(re.findall(r"@(?:app|router)\.(?:get|post|put|delete|patch)\(", all_text)),
            "typer_commands": len(re.findall(r"@\w+(?:_app)?\.command\(", all_text)),
            "sqlite_tables": len(re.findall(r"CREATE TABLE IF NOT EXISTS", all_text)),
            "workflows": len(workflows),
            "workflow_names": sorted(workflows),
            "builtin_skills": len(skills),
            "builtin_skill_names": skills,
            "delivery_standard_sections": sum(
                "## Delivery Standard" in (skill_root / name / "SKILL.md").read_text(encoding="utf-8")
                for name in skills
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(collect(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
