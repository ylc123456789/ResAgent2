"""Components can be used without importing model-facing tools or Agents."""

import ast
import inspect
from pathlib import Path

import resagent2_components


PACKAGE_ROOT = Path(__file__).parents[2] / "packages/components/src"
FORBIDDEN_ROOTS = {
    "resagent2_capabilities", "resagent2_orchestrator", "resagent2_coding",
    "resagent2_experiment", "resagent2_scientific", "resagent2_cli",
}


def test_components_do_not_import_tools_agents_or_orchestrator() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not roots & FORBIDDEN_ROOTS, (path, roots)


def test_public_components_are_not_tool_entry_points() -> None:
    for name in resagent2_components.__all__:
        value = getattr(resagent2_components, name)
        if inspect.isclass(value):
            assert inspect.getdoc(value), name
            assert not hasattr(value, "input_model"), name
