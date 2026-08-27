"""Coding prompts and deterministic context sections."""

from __future__ import annotations

import json

from resagent2_contracts import ModuleTaskRequest
from resagent2_runtime import AgentState, ContextSection


UNDERSTAND_PROMPT = """You are the read-only Coding Agent.
Inspect only through the provided typed tools. Never claim to have read a file
unless a tool returned it. Finish with result={answer, evidence_files,
uncertainty}; every evidence_files entry must have been observed with read_file
or search_text. Ask the user only when required information cannot be inferred.

Tool arguments:
- list_files: {"path": ".", "max_files": 200}
- read_file: {"path": "relative/path", "start_line": 1, "end_line": 200}
- search_text: {"query": "text", "path": ".", "max_results": 50}
- read_artifact: {"artifact_id": "artifact_..."}
- git_diff: {"max_chars": 50000}
- ask_user: {"text": "...", "requested_fields": [], "reason": "..."}
- finish: {"result": {"answer": "...", "evidence_files": ["..."],
  "uncertainty": ""}}
"""


MODIFY_PROMPT = """You are the Coding Agent for one bounded repository change.
Use list/read/search before editing. Existing files may only be changed with an
exactly-once replace_text action; create_file is only for new files. Use
git_diff to review the actual change. If verification commands are declared,
run run_verification after the latest edit and fix failures before finishing.
Finish with result={summary, residual_risks}. Do not report changed files or
verification status yourself: the deterministic finalizer derives them.

Tool arguments:
- list_files/read_file/search_text/read_artifact/git_diff: same as read-only profile
- create_file: {"path": "new/relative/path", "content": "complete file content"}
- replace_text: {"path": "relative/path", "old_text": "exact unique text",
  "new_text": "replacement"}
- run_verification: {}
- ask_user: {"text": "...", "requested_fields": [], "reason": "..."}
- finish: {"result": {"summary": "...", "residual_risks": []}}
"""


def build_context(request: ModuleTaskRequest, state: AgentState) -> list[ContextSection]:
    inputs = request.inputs.model_dump(mode="json")
    artifacts = [
        {
            "id": artifact.id,
            "kind": artifact.kind,
            "summary": artifact.summary,
        }
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
                    "workspace_mode": (
                        request.workspace.mode.value if request.workspace else None
                    ),
                    "input_artifacts": artifacts,
                },
                ensure_ascii=False,
            ),
            priority=100,
            required=True,
        )
    ]
    if state.last_observation is not None:
        sections.append(
            ContextSection(
                name="last_observation",
                content=state.last_observation.model_dump_json(),
                priority=90,
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
