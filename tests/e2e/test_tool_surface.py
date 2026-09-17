"""Freeze the model-visible tool surface at the pre-refactor commit 678b03f.

Descriptions include class docstrings and guidance, not only input schemas.
The fixture is a regression baseline, not permission to auto-update snapshots.
"""

import hashlib
import json
from pathlib import Path

from resagent2_capabilities import (
    AuditEnvTool,
    CreateFileTool,
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
        "coding": (RunVerificationTool,),
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


def test_model_visible_tool_surface_is_unchanged() -> None:
    baseline = json.loads(Path(__file__).with_name("tool_surface_678b03f.json").read_text())
    assert tool_surface_fingerprints() == baseline
