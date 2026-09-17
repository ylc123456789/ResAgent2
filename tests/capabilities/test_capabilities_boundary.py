import ast
import inspect
from pathlib import Path

import resagent2_capabilities
from resagent2_runtime.models import RuntimeModel


PACKAGE_ROOT = Path(__file__).parents[2] / "packages" / "capabilities" / "src"
ALLOWED_IMPORT_ROOTS = {
    "resagent2_components",
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "defusedxml",
    "email",
    "hashlib",
    "json",
    "logging",
    "mimetypes",
    "os",
    "pathlib",
    "platform",
    "shlex",
    "shutil",
    "signal",
    "subprocess",
    "tempfile",
    "threading",
    "time",
    "typing",
    "urllib",
    "uuid",
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


def test_capabilities_export_only_tools_and_their_inputs() -> None:
    """Operations must be imported from components, not forwarded by tools."""
    public = [getattr(resagent2_capabilities, name) for name in resagent2_capabilities.__all__]
    tools = [value for value in public if hasattr(value, "input_model")]
    inputs = {tool.input_model for tool in tools}
    assert set(public) == set(tools) | inputs
    assert all(issubclass(value, RuntimeModel) for value in inputs)
    assert all(callable(tool.execute) for tool in tools)


def test_capabilities_do_not_define_operation_classes() -> None:
    """Small tool helpers are fine; service classes belong in components."""
    for source_file in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            is_input = any(isinstance(base, ast.Name) and base.id == "RuntimeModel" for base in node.bases)
            is_tool = any(isinstance(item, ast.FunctionDef) and item.name == "execute" for item in node.body)
            assert is_input or is_tool, (source_file, node.name)
