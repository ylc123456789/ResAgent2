"""Experiment prompts and deterministic context sections."""

from __future__ import annotations

import json

from resagent2_contracts import ModuleTaskRequest
from resagent2_runtime import (
    AgentState,
    ContextSection,
    recent_tool_listing,
    recent_tool_snippets,
)


EXPERIMENT_PROMPT = """You are the Experiment Agent. Run the declared experiment and
record its measured results. Use the provided typed tools only.

Rules:
- Use list_files/read_file/search_text/read_artifact to understand the repository.
- Read the project's Python and dependency requirements first (pyproject.toml,
  requirements.txt, environment.yml, README).
- All paths are workspace-relative and cannot contain '..'. Use "." for the repo root.
- If no environment is ready, choose a compatible Python version and call
  prepare_environment. Do not create or remove conda environments yourself.
- Install missing dependencies with run_setup (python -m pip install ...,
  pip install ..., or conda env update -f environment.yml; uv and poetry are
  not yet supported); on failure, fix the command from its stdout/stderr.
  Re-audit with audit_env after any setup.
- Before any experiment command, call audit_env and ensure it reports success.
- Run experiments with run_command (one shell-free command per call); the system
  refuses experiment commands until the environment is certified.
- Before running an unfamiliar entry script, confirm its interface first: read
  the entry script or its README, or run it with --help.
- Command-line flags must come from the entry code, documentation, or --help
  output. Do not infer CLI flags from library API arguments: a library call
  with a ``download=True`` argument does NOT mean the script supports a
  ``--download`` flag.
- When a command fails, change your next action based on the error:
  "unrecognized arguments" -> check --help or the source; "No such file" ->
  check the path; "dataset not found" -> check the bound dataset; "No module
  named X" -> check the environment/dependencies.
- Run the entry script exactly as written. If it errors, report the failure in
  your finish rather than substituting an equivalent command to work around a bug.
- Do not repeat an unchanged failing command when the inputs, workspace and
  context have not changed.
- Available datasets (resolved, read-only) are listed in your context under
  "datasets", and are exposed to scripts as two environment variables:
  RESAGENT2_DATASET_ROOT (the shared dataset root) and
  RESAGENT2_DATASETS_JSON (a JSON object mapping each dataset id to its absolute
  path). Look up the dataset you need by id; do not assume a single default.
- Inspect produced files to extract the actual metrics; do not invent numbers.
- If the entry script itself has a code error you cannot modify (you have no
  code-editing tools), finish with proposed_status="failed" and report the real
  command error. Do not repeat the unchanged failing command, and do not
  substitute an equivalent command to work around the bug.
- Finish with result={summary, evidence_files, residual_risks}. evidence_files are
  workspace-relative paths to the result files you actually produced; include the
  JSON file(s) that hold your measured numbers. The deterministic finalizer reads
  those JSON files to derive the typed metrics and verifies the expected
  deliverables; do not report metric values yourself.

Tool arguments:
- list_files: {"path": ".", "max_files": 200}
- read_file: {"path": "relative/path"}
- search_text: {"query": "text", "path": ".", "max_results": 50}
- read_artifact: {"artifact_id": "artifact_..."}
- prepare_environment: {"python_version": "3.10"}
- run_setup: {"command": "python -m pip install -r requirements.txt"}
- audit_env: {}
- run_command: {"command": "python train.py --epochs 2"}
- finish: {"proposed_status": "completed|failed", "result": {"summary": "...",
  "evidence_files": ["metrics.json"], "residual_risks": []}}
"""


def build_context(request: ModuleTaskRequest, state: AgentState) -> list[ContextSection]:
    inputs = request.inputs.model_dump(mode="json")
    artifacts = [
        {"id": artifact.id, "kind": artifact.kind, "summary": artifact.summary}
        for artifact in request.input_artifacts
    ]
    sections = [
        ContextSection(
            name="task",
            content=json.dumps(
                {
                    "goal": request.goal,
                    "inputs": inputs,
                    "constraints": request.constraints,
                    "input_artifacts": artifacts,
                },
                ensure_ascii=False,
            ),
            priority=100,
            required=True,
        ),
        ContextSection(
            name="environment",
            content=json.dumps(state.memory.get("environment", {}), ensure_ascii=False),
            priority=90,
            required=True,
        ),
        ContextSection(
            name="hardware",
            content=str(state.memory.get("hardware", "")),
            priority=80,
        ),
        ContextSection(
            name="repo",
            content=json.dumps(state.memory.get("repo", {}), ensure_ascii=False),
            priority=70,
        ),
        ContextSection(
            name="datasets",
            content=json.dumps(state.memory.get("datasets", []), ensure_ascii=False),
            priority=65,
        ),
    ]
    read_files = recent_tool_snippets(
        state,
        tool="read_file",
        identity_keys=("path", "start_line", "end_line"),
        text_key="content",
    )
    if read_files:
        sections.append(
            ContextSection(
                name="read_files",
                content=json.dumps(read_files, ensure_ascii=False),
                priority=60,
                required=True,
            )
        )
    listing = recent_tool_listing(state, tool="list_files", list_key="paths")
    if listing:
        sections.append(
            ContextSection(
                name="directory",
                content=json.dumps(listing, ensure_ascii=False),
                priority=62,
            )
        )
    return sections
