import ast
import inspect
from pathlib import Path

import resagent2_orchestrator


PACKAGE_ROOT = Path(__file__).parents[2] / "packages" / "orchestrator" / "src"
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "hashlib",
    "importlib",
    "json",
    "os",
    "pathlib",
    "shutil",
    "sys",
    "tempfile",
    "typing",
    "pydantic",
    "resagent2_contracts",
}


def test_orchestrator_does_not_import_runtime_or_specific_agents() -> None:
    imported_roots: set[str] = set()
    for source_file in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_roots.add(node.module.split(".")[0])
    assert imported_roots <= ALLOWED_IMPORT_ROOTS


def test_every_public_orchestrator_class_has_a_docstring() -> None:
    missing = []
    for name in resagent2_orchestrator.__all__:
        value = getattr(resagent2_orchestrator, name)
        if inspect.isclass(value) and not inspect.getdoc(value):
            missing.append(name)
    assert missing == []
