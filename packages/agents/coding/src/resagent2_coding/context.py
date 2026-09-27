"""Coding's single prompt and deterministic context."""

from __future__ import annotations

import json

from resagent2_components import (
    DatasetAvailability, EnvironmentBinding, dataset_context, workspace_context,
    request_materials_context,
)
from resagent2_contracts import AgentRequest
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentState, ContextMaterial, ContextSection,
)


CODING_PROMPT = """You are the Coding Agent. Inspect authorized source and registered
materials before claims or edits. Explain, investigate or change as needed;
write access does not require changes.
Verify code correctness with bounded checks. Formal research measurements belong
to Experiment, even when wrapped in tests. Deliver implemented entry points and
actual checks; report any remaining experimental work.

Use replace_text for existing files, create_file for new files and delete_path
for deletions; nonempty directories require confirmation.
old_text must match exactly once in the current file per call.
You may make multiple replace_text calls as needed. Review the actual diff.
Read dependency requirements before choosing Python with prepare_environment.
Install with run_setup, then run_verification. Verification automatically audits
the environment, including after approval; use audit_env only for diagnostics.
Verification accepts shell-free tests such as python -m pytest, unittest,
py_compile or compileall. For import checks, write a unittest;
python -c and arbitrary scripts are not allowed verification commands.
Report verification pass/fail separately from freshness; never claim unexecuted
or stale success. Fulfil explicit artifact requirements, including output_name.

Finish with status ("completed" or "failed"), report and artifacts. Judge task
completion from the objective and evidence; a failed check alone is not decisive.
Explain findings, changes, verification, remaining work and uncertainty.
Candidates contain kind, relative path, media_type, summary,
optional output_name and optional UTF-8 content. Use files for substantive
source/results and content for short structured outputs. The system derives
patches, changed files and verification records from execution; never fabricate
them. Read-only source access still permits this controlled output channel.
Ask the user only for required information you cannot infer.
"""


def build_context(
    request: AgentRequest,
    state: AgentState,
    *,
    control_state: dict | None = None,
    binding: EnvironmentBinding | None = None,
    datasets: DatasetAvailability | None = None,
    max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS,
) -> list[ContextSection | ContextMaterial]:
    sections = [
        ContextSection(
            name="task",
            content=json.dumps({
                "instruction": request.instruction,
                "workspace_access": request.workspace.access.model_dump(mode="json") if request.workspace else None,
                "confirm_commands": request.confirm_commands,
                "permissions": request.permissions.model_dump(mode="json"),
                "input_artifacts": [
                    {"id": item.id, "kind": item.kind, "summary": item.summary}
                    for item in request.input_artifacts
                ],
            }, ensure_ascii=False),
            priority=100, required=True,
        ),
        ContextSection(
            name="dataset_catalog",
            content=json.dumps(dataset_context(datasets or DatasetAvailability())),
            priority=90, required=True,
        ),
        *request_materials_context(request),
    ]
    if control_state is not None:
        sections.append(ContextSection(
            name="verification_state",
            content=json.dumps(control_state),
            priority=95, required=True,
        ))
    sections.extend(workspace_context(
        state, binding=binding, max_context_tokens=max_context_tokens,
    ))
    return sections
