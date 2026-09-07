"""Capability registry keeps ownership separate from execution policies."""

import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
)


def test_minimal_registry_round_trip_and_unique_owner() -> None:
    definition = CapabilityDefinition(
        capability=Capability.CODE_MODIFY,
        owner=AgentOwner.CODING,
        description="Change code and verify the result.",
    )
    registry = CapabilityRegistry(definitions=[definition])

    assert CapabilityRegistry.model_validate_json(registry.model_dump_json()) == registry

    other_owner = definition.model_copy(update={"owner": AgentOwner.EXPERIMENT})
    with pytest.raises(ValidationError, match="duplicate capability definition"):
        CapabilityRegistry(definitions=[definition, other_owner])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_model", "CodeModifyInput"),
        ("result_model", "CodeModifyResult"),
        ("side_effects", ["workspace_write"]),
        ("permission_policy", "read_write_workspace"),
        ("completion_evidence", ["code_change"]),
    ],
)
def test_registry_rejects_removed_execution_declarations(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as error:
        CapabilityDefinition.model_validate(
            {
                "capability": Capability.CODE_MODIFY,
                "owner": AgentOwner.CODING,
                field: value,
            }
        )

    assert any(
        item["type"] == "extra_forbidden" and item["loc"] == (field,)
        for item in error.value.errors()
    )
