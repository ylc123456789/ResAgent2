import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    WorkflowAgentDefinition, WorkflowAgentKind, WorkflowAgentRegistry,
)


def test_registry_contains_execution_modules_only():
    registry = WorkflowAgentRegistry(definitions=[
        WorkflowAgentDefinition(workflow_agent_kind=kind)
        for kind in WorkflowAgentKind
    ])
    assert [item.workflow_agent_kind.value for item in registry.definitions] == [
        "coding", "experiment",
    ]
    assert WorkflowAgentRegistry.model_validate_json(registry.model_dump_json()) == registry
    with pytest.raises(ValidationError):
        WorkflowAgentDefinition(workflow_agent_kind="scientific")


def test_registry_rejects_duplicate_module():
    item = WorkflowAgentDefinition(workflow_agent_kind="coding")
    with pytest.raises(ValidationError, match="duplicate"):
        WorkflowAgentRegistry(definitions=[item, item])
