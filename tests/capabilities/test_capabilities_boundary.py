import ast
import inspect
from pathlib import Path

import resagent2_capabilities


PACKAGE_ROOT = Path(__file__).parents[2] / "packages" / "capabilities" / "src"
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "dataclasses",
    "hashlib",
    "json",
    "mimetypes",
    "os",
    "pathlib",
    "platform",
    "shlex",
    "shutil",
    "signal",
    "subprocess",
    "tempfile",
    "time",
    "typing",
    "urllib",
    "pydantic",
    "resagent2_contracts",
    "resagent2_runtime",
}


def test_capabilities_do_not_import_orchestrator_or_agents() -> None:
    imported_roots: set[str] = set()
    for source_file in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_roots.add(node.module.split(".")[0])

    assert imported_roots <= ALLOWED_IMPORT_ROOTS


def test_every_public_capabilities_class_has_a_docstring() -> None:
    missing = [
        name
        for name in resagent2_capabilities.__all__
        if inspect.isclass(getattr(resagent2_capabilities, name))
        and not inspect.getdoc(getattr(resagent2_capabilities, name))
    ]

    assert missing == []
