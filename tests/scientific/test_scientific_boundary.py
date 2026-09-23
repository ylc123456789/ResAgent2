"""Keep Scientific independent of other Agents and the control implementation."""

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[2] / "packages/agents/scientific/src"
ALLOWED_IMPORT_ROOTS = {
    "__future__", "collections", "json", "typing", "pydantic",
    "resagent2_contracts", "resagent2_runtime", "resagent2_components",
    "resagent2_capabilities",
}


def test_scientific_does_not_import_orchestrator_or_other_agents() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert roots <= ALLOWED_IMPORT_ROOTS, (path, roots - ALLOWED_IMPORT_ROOTS)
