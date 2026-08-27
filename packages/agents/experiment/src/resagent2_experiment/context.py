"""Experiment prompts and deterministic context sections."""

from __future__ import annotations

import json

from resagent2_contracts import ModuleTaskRequest
from resagent2_runtime import AgentState, ContextSection


EXPERIMENT_PROMPT = """You are the Experiment Agent. Run the declared experiment and
record its measured results. Use the provided typed tools only.

Rules:
- Use list_files/read_file/search_text/read_artifact to understand the repository.
- Before any experiment command, call audit_env and ensure it reports success.
- Run commands with run_command (one shell-free command per call). The system
  refuses experiment commands until the environment is certified.
- Inspect produced files to extract the actual metrics; do not invent numbers.
- Finish with result={summary, metrics, parameters, evidence_files, residual_risks}.
  evidence_files are workspace-relative paths to the result files you actually
  produced. The deterministic finalizer verifies files and expected deliverables.

Tool arguments:
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
