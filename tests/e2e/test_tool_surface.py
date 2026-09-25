"""Freeze the model-visible tool surface after the unified Agent IO migration.

Descriptions include class docstrings and guidance, not only input schemas.
The fixture is a regression baseline, not permission to auto-update snapshots.
Schema 13 updates control schema constants and adds the Coding delete_path tool.
Other workspace, artifact, literature and environment tools retain their surface.
Schema 14, 15, 16 and 17 only update the embedded public schema version constants.
"""

import hashlib
import json
from pathlib import Path

from resagent2_capabilities import (
    AuditEnvTool,
    CreateFileTool,
    DeletePathTool,
    GitDiffTool,
    ListFilesTool,
    LiteratureSearchTool,
    PrepareEnvironmentTool,
    ReadArtifactTool,
    ReadFileTool,
    ReplaceTextTool,
    RunSetupTool,
    SearchTextTool,
)
from resagent2_coding.verification import RunVerificationTool
from resagent2_experiment.tools import RunCommandTool
from resagent2_runtime import AskUserTool, FinishTool
from resagent2_runtime.tool_calling import native_tool_schemas
from resagent2_scientific.tools import (
    AskUserTool as ScientificAskUserTool,
    FinishTool as ScientificFinishTool,
    RequestWorkTool,
)


def tool_surface_fingerprints() -> dict[str, str]:
    groups = {
        "shared": (AuditEnvTool, CreateFileTool, GitDiffTool, ListFilesTool,
                   LiteratureSearchTool, PrepareEnvironmentTool, ReadArtifactTool,
                   ReadFileTool, ReplaceTextTool, RunSetupTool, SearchTextTool),
        "coding": (RunVerificationTool, DeletePathTool),
        "experiment": (RunCommandTool,),
        "runtime": (AskUserTool, FinishTool),
        "scientific": (ScientificAskUserTool, ScientificFinishTool, RequestWorkTool),
    }
    result = {}
    for group, classes in groups.items():
        for cls in classes:
            # Schema/description are class-level; no I/O or execution is needed.
            schema = native_tool_schemas((object.__new__(cls),))[0]
            text = json.dumps(schema, sort_keys=True, ensure_ascii=False)
            result[f"{group}/{cls.name}"] = hashlib.sha256(text.encode()).hexdigest()
    return result


def test_model_visible_tool_surface_matches_unified_protocol() -> None:
    baseline = json.loads(Path(__file__).with_name("tool_surface_unified_v2.json").read_text())
    assert tool_surface_fingerprints() == baseline


def test_unified_finish_has_one_schema_for_all_agents() -> None:
    assert ScientificFinishTool is FinishTool
    assert set(FinishTool.input_model.model_fields) == {"report", "artifacts"}
