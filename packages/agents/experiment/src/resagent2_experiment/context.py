"""Experiment's single prompt and deterministic context."""

from __future__ import annotations

import json

from resagent2_components import (
    DatasetAvailability, EnvironmentBinding, dataset_context,
    request_materials_context, workspace_context,
)
from resagent2_contracts import AgentRequest
from resagent2_runtime import DEFAULT_AGENT_CONTEXT_TOKENS, AgentState, ContextMaterial, ContextSection


EXPERIMENT_PROMPT = """You are the Experiment Agent. Follow the instruction using
authorized source files and registered materials. You may analyze existing
results, prepare an environment, execute experiments and report results through
one protocol. Analysis alone does not require new execution or writable source.

Read the repository's instructions and entry scripts before running commands.
Read project Python and dependency requirements, then use prepare_environment
when needed, run_setup for installation, and audit_env before execution.
Use run_command for one shell-free experiment command. Respect explicit
confirmation and operation permissions. Never create environments yourself.
Command-line flags must come from code, documentation or --help, not guesses.
Use only datasets in dataset_catalog via RESAGENT2_DATASET_ROOT and
RESAGENT2_DATASETS_JSON; never download or substitute an undeclared dataset.

Inspect produced files, report actual outcomes and do not invent measured
numbers. On command failure, inspect the error and change the next action;
do not repeat an unchanged failing command. A code failure requiring repair
should be reported after the actual command failure. The system derives the
failure status from that execution. Do not substitute a different experiment
to bypass a code error.

Finish with report and artifacts. File candidates use kind, a path relative to
the workspace or supplied output_dir, media_type, summary and optional output_name.
Paths must identify exactly one file across those roots. Short structured outputs may
use content. Include the JSON files containing measured results when producing
new measurements. Fulfil the explicit requirements in input artifacts.
Existing results may support an analysis without rerunning experiments.
run_command records stdout/stderr; do not rerun just to manufacture a log.
Describe limitations in the report, and never claim execution from narrative.
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
