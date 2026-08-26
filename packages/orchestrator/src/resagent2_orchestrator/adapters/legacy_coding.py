"""Adapter for the old CodingAgent module.

DELETION CONDITION: delete once Phase 5 ``Coding Agent vNext`` replaces it with a
native Agent that reuses the shared runtime. This adapter only translates the
public request/result shapes; it must not copy CodingAgent's edit/verify logic.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from resagent2_contracts import (
    ArtifactCandidate,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    QuestionDraft,
)

_MODEL = "deepseek-chat"
_API_BASE = "https://api.deepseek.com/v1"
_API_KEY_ENV = "DEEPSEEK_API_KEY"


def _output_dir(request: ModuleTaskRequest) -> Path:
    base = Path(request.workspace.root) if request.workspace else Path.cwd()
    return base / f"{request.task_id}_attempt{request.attempt_number}"


class LegacyCodingAdapter:
    """Map ModuleTaskRequest <-> CodingAgent CodeTaskSpec / PatchReport."""

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        root = os.environ.get("CODINGAGENT_PATH", "/root/autodl-tmp/projects/CodingAgent")
        src = str(Path(root) / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        models = importlib.import_module("coding_agent.models")
        agent = importlib.import_module("coding_agent.agent")

        inputs = request.inputs  # CodeModifyInput
        spec = models.CodeTaskSpec(
            workspace_path=Path(request.workspace.root) if request.workspace else Path("."),
            task_goal=request.goal,
            output_dir=_output_dir(request),
            allowed_paths=list(inputs.allowed_paths),
            verify_commands=list(inputs.verification_commands),
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
        )
        report = agent.run_code_task(spec)
        return self.from_result(report.model_dump(mode="json"))

    @staticmethod
    def to_spec(request: ModuleTaskRequest) -> dict:
        inputs = request.inputs  # CodeModifyInput
        return {
            "task_goal": request.goal,
            "workspace_path": request.workspace.root if request.workspace else None,
            "allowed_paths": list(inputs.allowed_paths),
            "verify_commands": list(inputs.verification_commands),
        }

    @staticmethod
    def from_result(raw: dict) -> ModuleResult:
        status = raw["status"]
        summary = raw.get("summary") or status
        if status == "completed":
            artifacts = [
                ArtifactCandidate(
                    kind="code_change",
                    path=path,
                    media_type="text/plain",
                    summary="changed file",
                )
                for path in raw.get("changed_files", [])
            ]
            return ModuleResult(
                status=ModuleStatus.COMPLETED,
                summary=summary,
                payload={
                    "changed_files": raw.get("changed_files", []),
                    "produced_files": raw.get("produced_files", []),
                    "residual_risks": raw.get("residual_risks", []),
                },
                artifacts=artifacts,
            )
        if status == "blocked":
            return ModuleResult(
                status=ModuleStatus.BLOCKED,
                summary=summary,
                error=ModuleError(
                    code=ErrorCode.ENVIRONMENT_UNAVAILABLE,
                    message=raw.get("message", summary),
                    retryable=False,
                ),
            )
        if status == "needs_user_input":
            return ModuleResult(
                status=ModuleStatus.NEEDS_USER_INPUT,
                summary=summary,
                question=QuestionDraft(
                    text=summary,
                    reason="legacy coding module requested user input",
                ),
            )
        return ModuleResult(
            status=ModuleStatus.FAILED,
            summary=summary,
            error=ModuleError(
                code=ErrorCode.TOOL_FAILED,
                message=raw.get("message", summary),
                retryable=True,
            ),
        )
