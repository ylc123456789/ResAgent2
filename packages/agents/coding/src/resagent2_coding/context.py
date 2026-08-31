"""Coding prompts and deterministic context sections."""

from __future__ import annotations

import json

from resagent2_contracts import ModuleTaskRequest
from resagent2_runtime import AgentState, ContextSection, recent_tool_text_values


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
- git_diff: {"max_chars": 20000}
- ask_user: {"text": "...", "requested_fields": [], "reason": "..."}
- finish: {"result": {"answer": "...", "evidence_files": ["..."],
  "uncertainty": ""}}
"""


MODIFY_PROMPT = """You are the Coding Agent for one bounded repository change.
Use list/read/search before editing. Read the project's Python and dependency
requirements first (pyproject.toml, requirements.txt, environment.yml, README).
If no environment is ready, choose a compatible Python version and call
prepare_environment; do not run conda create/remove yourself. Install missing
dependencies with run_setup (python -m pip install ..., pip install ..., or
conda env update -f environment.yml; uv and poetry are not yet supported).
Re-audit with audit_env after any setup.
Existing files may only be changed with an exactly-once replace_text action;
create_file is only for new files. Use git_diff to review the actual change.
After the latest edit, run shell-free verification commands inside the bound
environment (python -m pytest / unittest / py_compile, or a small import smoke
check), then fix any failures before finishing. Finish with
result={summary, residual_risks}. Do not report changed files or verification
status yourself: the deterministic finalizer derives them.

Tool arguments:
- list_files/read_file/search_text/read_artifact/git_diff: same as read-only profile
- prepare_environment: {"python_version": "3.10"}
- run_setup: {"command": "python -m pip install -r requirements.txt"}
- audit_env: {}
- create_file: {"path": "new/relative/path", "content": "complete file content"}
- replace_text: {"path": "relative/path", "old_text": "exact unique text",
  "new_text": "replacement"}
- run_verification: {"commands": ["python -m pytest", "python -m py_compile train.py"]}
- ask_user: {"text": "...", "requested_fields": [], "reason": "..."}
- finish: {"result": {"summary": "...", "residual_risks": []}}
"""


def build_context(
    request: ModuleTaskRequest,
    state: AgentState,
    *,
    control_state: dict | None = None,
) -> list[ContextSection]:
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
    read_files = recent_tool_text_values(
        state, tool="read_file", identity_key="path", text_key="content"
    )
    if read_files:
        sections.append(
            ContextSection(
                name="read_files",
                content=json.dumps(read_files, ensure_ascii=False),
                priority=80,
                required=True,
            )
        )
    if control_state is not None:
        sections.insert(
            0,
            ContextSection(
                name="control_state",
                content=(
                    "Current coding control state (deterministic — do not "
                    "invent your own):\n"
                    + json.dumps(control_state, ensure_ascii=False)
                ),
                priority=1000,
                required=True,
            ),
        )
    return sections
