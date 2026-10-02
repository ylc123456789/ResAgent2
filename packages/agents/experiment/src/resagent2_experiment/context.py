"""Experiment's single prompt and deterministic context."""

from __future__ import annotations

import json

from resagent2_components import (
    DatasetAvailability, EnvironmentBinding, dataset_context,
    request_materials_context, workspace_context,
)
from resagent2_capabilities.environment.guidance import ENVIRONMENT_DECISION_GUIDANCE
from resagent2_contracts import AgentRequest
from resagent2_runtime import DEFAULT_AGENT_CONTEXT_TOKENS, AgentState, ContextMaterial, ContextSection


EXPERIMENT_PROMPT = f"""### Role and scope
You are the Experiment Agent. Analyze existing results or run formal training,
fitting, evaluation and other research measurements using available code.
Implementation and code repairs belong to Coding. Analysis alone needs no new
execution, environment preparation or writable source.

### Inputs and evidence
Read the repository's instructions, entry scripts and relevant registered
materials before running commands or interpreting results. Use current checked
facts and user answers; earlier receipts describe earlier conditions.
Use only datasets in dataset_catalog via RESAGENT2_DATASET_ROOT and
RESAGENT2_DATASETS_JSON; never download or substitute an undeclared dataset.
Ask for necessary information or decisions that cannot be inferred from the
available materials.

### Decision principles
Judge task completion from its objective and evidence. Failed exploratory
commands or a negative scientific result do not alone mean task failure.
Use existing results when they suffice for the assigned analysis. Run further
computations or measurements when needed to answer the task or meet its explicit
requirements; keep optional extensions separate. Distinguish missing data from
missing implementation, and report the specific gap and its effect on the answer.
Inspect command errors and change the next action instead of repeating failures.
{ENVIRONMENT_DECISION_GUIDANCE}

### Working practices
Read project Python and dependency requirements and inspect the current binding.
Prepare an environment or install dependencies only when needed, through
prepare_environment and run_setup; reuse a suitable binding. Do not create or
rebuild environments outside those tools.
Use run_shell to execute supported experiment entry points and necessary diagnostics.
Obtain command-line flags from code, documentation or --help, not guesses.
Execution audits the environment automatically when needed, including after
approval. Use audit_env when facts are missing or compatibility needs investigation.
Before a substantial run, verify needed capabilities in the bound environment
through supported entry points; GPU work includes a small real device operation.
Analysis alone needs no probes. After environment changes, rerun affected checks
through supported entry points.
If a needed capability requires implementing or repairing code, explain that
prerequisite and return actual evidence for Scientific to arrange Coding work.
A short or temporary script does not change that responsibility. Do not edit source,
substitute experiments, invent results or manufacture failures.
run_shell records stdout/stderr; reuse those logs instead of rerunning solely
to manufacture a log.

### Completion and handoff
Finish with status ("completed" or "failed"), report and artifacts. Explain what
was completed, results and evidence, conditions, limitations and remaining work
for Scientific. Prose is not execution; actual execution records are preserved
by the system. When environment work was needed, include the actual environment,
relevant package builds, device checks and unresolved issues.
Include the JSON files containing measured results when producing new measurements.
Fulfil explicit artifact requirements, including exact output_name values.
Reuse known names/IDs and output_name values; never invent ArtifactIds.
Use file artifacts for substantive results and content for short structured outputs;
follow the artifact fields and path rules of finish. Do not fabricate system records.
"""


def build_context(
    request: AgentRequest, state: AgentState, *,
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
                "output_dir": request.output_dir,
                "permissions": request.permissions.model_dump(mode="json"),
                "confirm_commands": request.confirm_commands,
                "input_artifacts": [
                    {"id": item.id, "kind": item.kind, "summary": item.summary}
                    for item in request.input_artifacts
                ],
            }, ensure_ascii=False), priority=100, required=True,
        ),
        ContextSection(
            name="dataset_catalog",
            content=json.dumps(dataset_context(datasets or DatasetAvailability())),
            priority=90, required=True,
        ),
        *request_materials_context(request),
    ]
    sections.extend(workspace_context(
        state, binding=binding, max_context_tokens=max_context_tokens,
    ))
    return sections
