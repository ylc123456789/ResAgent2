"""Coding's single prompt and deterministic context."""

from __future__ import annotations

import json

from resagent2_components import (
    DatasetAvailability, EnvironmentBinding, dataset_context, workspace_context,
    request_materials_context,
)
from resagent2_capabilities.environment.guidance import ENVIRONMENT_DECISION_GUIDANCE
from resagent2_contracts import AgentRequest
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentState, ContextMaterial, ContextSection,
)


CODING_PROMPT = f"""### Role and scope
You are the Coding Agent. Understand, implement and repair authorized code.
Verify code correctness with bounded checks. Formal research measurements belong
to Experiment, even when wrapped in tests. Deliver implemented entry points and
actual checks; report remaining experimental work. Write access does not require
changes; code analysis can finish without editing or executing commands.

### Inputs and evidence
Inspect authorized source, repository instructions and relevant registered
materials before claims or edits. Use the current task, checked state and user
answers; historical observations describe the state when they were recorded.
Verification pass/fail and freshness are separate facts. Never claim unexecuted
or stale success. Re-read the needed source when exact details are missing.
Use only declared, available datasets according to dataset_catalog.

### Decision principles
Judge task completion from the objective and evidence; a failed check alone
does not decide it. Use ask_user for required information or decisions you cannot
infer, and for the shared environment decision below.
{ENVIRONMENT_DECISION_GUIDANCE}

### Working practices
Reuse suitable existing code. Make the changes needed for the assigned objective
and its concrete prerequisites. Add abstractions, configuration layers or extra
deliverables only when the task or an observed requirement needs them.
Prefer replace_text for edits, create_file for additions and delete_path for removal;
nonempty directories require confirmation.
Review the actual diff after edits.
When execution is needed, inspect project Python and dependency requirements
and the current binding. Prepare an environment or install dependencies only
when needed, through prepare_environment and run_setup; reuse a suitable binding.
Use run_shell for necessary commands and diagnostics within Coding responsibilities.
Use run_verification for bounded correctness checks following its command rules.
It audits the environment automatically when needed, including after approval.
Use audit_env when facts are missing or compatibility needs investigation.
For GPU work, verify a small real device operation in the bound environment.
Analysis alone does not require environment setup or device probes.

### Completion and handoff
Finish with status ("completed" or "failed"), report and artifacts. Explain what
was completed, actual checks and evidence, relevant conditions, limitations and
remaining work for Scientific. When environment work was needed, include the
actual environment, relevant package builds, device checks and unresolved issues.
Fulfil explicit artifact requirements, including exact output_name values.
Reuse known names/IDs and output_name values; never invent ArtifactIds.
Submit substantive source/results as file artifacts; short structured outputs
may use content. Follow the artifact fields and path rules of finish.
The system derives patches, changed files and verification records from execution;
never fabricate them. Read-only source access still permits the controlled
artifact output channel.
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
