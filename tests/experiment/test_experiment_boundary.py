import ast
import inspect
from pathlib import Path

import resagent2_experiment


PACKAGE_ROOT = Path(__file__).parents[2] / "packages" / "agents" / "experiment" / "src"
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "hashlib",
    "json",
    "pathlib",
    "pydantic",
    "resagent2_contracts",
    "resagent2_runtime",
    "typing",
}


def test_experiment_package_does_not_import_orchestrator_or_legacy_agent() -> None:
    imported_roots: set[str] = set()
    for source_file in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_roots.add(node.module.split(".")[0])

    assert imported_roots <= ALLOWED_IMPORT_ROOTS


def test_every_public_experiment_class_has_a_docstring() -> None:
    missing = [
        name
        for name in resagent2_experiment.__all__
        if inspect.isclass(getattr(resagent2_experiment, name))
        and not inspect.getdoc(getattr(resagent2_experiment, name))
    ]

    assert missing == []
