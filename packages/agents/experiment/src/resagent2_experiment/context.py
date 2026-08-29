"""Experiment prompts and deterministic context sections."""

from __future__ import annotations

import json

from resagent2_contracts import ModuleTaskRequest
from resagent2_runtime import AgentState, ContextSection


EXPERIMENT_PROMPT = """You are the Experiment Agent. Run the declared experiment and
record its measured results. Use the provided typed tools only.

Rules:
- Use list_files/read_file/search_text/read_artifact to understand the repository.
- All paths are workspace-relative and cannot contain '..'. Use "." for the repo root.
- Before any experiment command, call audit_env and ensure it reports success.
- Run commands with run_command (one shell-free command per call). The system
  refuses experiment commands until the environment is certified.
- Before running an unfamiliar entry script, confirm its interface first: read
  the entry script or its README, or run it with --help.
- Command-line flags must come from the entry code, documentation, or --help
  output. Do not infer CLI flags from library API arguments: a
  CIFAR10(download=True) call does NOT mean the script supports --download.
- When a command fails, change your next action based on the error:
  "unrecognized arguments" -> check --help or the source; "No such file" ->
  check the path; "dataset not found" -> check the bound dataset; "No module
  named X" -> check the environment/dependencies.
- Do not repeat an unchanged failing command when the inputs, workspace and
  context have not changed.
- Available datasets (resolved, read-only) are listed in your context under
  "datasets". Point the framework's dataset cache env var (e.g.
  TORCHVISION_DATASETS for torchvision) to the relevant dataset's path before
  running.
- Inspect produced files to extract the actual metrics; do not invent numbers.
- Finish with result={summary, metrics, parameters, evidence_files, residual_risks}.
  evidence_files are workspace-relative paths to the result files you actually
  produced. The deterministic finalizer verifies files and expected deliverables.

Tool arguments:
- list_files: {"path": ".", "max_files": 200}
- read_file: {"path": "relative/path"}
- search_text: {"query": "text", "path": ".", "max_results": 50}
- read_artifact: {"artifact_id": "artifact_..."}
- run_command: {"command": "python train.py --epochs 2"}
- audit_env: {}
- finish: {"result": {"summary": "...", "metrics": {"accuracy": 0.9},
  "parameters": {"epochs": 2}, "evidence_files": ["metrics.json"],
  "residual_risks": []}}
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
    if state.last_observation is not None:
        sections.append(
            ContextSection(
                name="last_observation",
                content=state.last_observation.model_dump_json(),
                priority=60,
                required=True,
            )
        )
    if state.memory:
        sections.append(
            ContextSection(
                name="audit_memory",
                content=json.dumps(state.memory, ensure_ascii=False),
                priority=50,
            )
        )
    return sections
