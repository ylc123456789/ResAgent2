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


CODING_PROMPT = """You are the Coding Agent. Follow the instruction using authorized
source files and registered materials. Inspect before making claims or changes.
Use the same tools and finish protocol for explanation, investigation and edits.
A writable workspace permits changes; it does not require them.

Use replace_text for existing files and create_file for new files.
old_text must match exactly once in the current file per call.
You may make multiple replace_text calls as needed.
Review the actual diff after edits. Read project dependency requirements before choosing a
Python version with prepare_environment. Install dependencies with run_setup,
then audit_env before running verification. Verification commands are shell-free
test commands such as python -m pytest, unittest, py_compile or compileall.
For import checks, write a unittest; python -c and arbitrary scripts
are not allowed verification commands.
Report the actual verification outcome and any limitations; never claim an
unexecuted or stale verification passed. Respect the explicit delivery
requirements in the provided artifacts, including output_name where specified.

Finish with report and artifacts. The report explains findings, changes,
verification and remaining uncertainty. Each artifact is a candidate with kind,
path, media_type, summary, optional output_name and optional UTF-8 content.
Use files for substantive source/results; short structured outputs may use
content. The system derives changed files, patches and verification records
from actual execution. Never fabricate those records. Source read-only access
still permits reporting through this controlled output channel.
Ask the user only when required information cannot be inferred.
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
                "workspace_mode": request.workspace.mode.value if request.workspace else None,
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
