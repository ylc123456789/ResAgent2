"""Adapter for the old reproagent module (experiment executor).

DELETION CONDITION: delete once Phase 6 ``Experiment Agent vNext`` replaces it.
This adapter only translates request/result shapes; it must not copy reproagent's
environment/process logic, and it must not hardcode conda/GPU details.
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
    WarningRecord,
)

_MODEL = "deepseek-chat"
_API_BASE = "https://api.deepseek.com/v1"
_API_KEY_ENV = "DEEPSEEK_API_KEY"


class LegacyExperimentAdapter:
    """Map ModuleTaskRequest <-> reproagent ReproTask / AgentState."""

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        root = os.environ.get("REPROAGENT_PATH", "/root/autodl-tmp/projects/reproagent")
        src = str(Path(root) / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        models = importlib.import_module("reproagent.models")
        agent = importlib.import_module("reproagent.agent")

        inputs = request.inputs  # ExperimentRunInput
        workspace = Path(request.workspace.root) if request.workspace else Path.cwd()
        task = models.ReproTask(
            workspace_dir=workspace,
            external_repo_path=os.environ.get(
                "REPROAGENT_EXTERNAL_REPO", str(workspace)
            ),
            experiment_goal=request.goal,
            expected_metrics=list(inputs.expected_metrics),
            expected_artifacts=list(inputs.expected_artifacts),
            success_criteria=[],
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
            env_name=os.environ.get("REPROAGENT_ENV_NAME", ""),
            parent_run={
                "module": "resagent",
                "run_id": request.run_id,
                "task_id": request.task_id,
            },
        )
        state = agent.run_task(task)
        return self.from_result(state.model_dump(mode="json"))

    @staticmethod
    def to_spec(request: ModuleTaskRequest) -> dict:
        inputs = request.inputs  # ExperimentRunInput
        return {
            "workspace_dir": request.workspace.root if request.workspace else None,
            "experiment_goal": request.goal,
            "expected_metrics": list(inputs.expected_metrics),
            "expected_artifacts": list(inputs.expected_artifacts),
            "parent_run": {
                "module": "resagent",
                "run_id": request.run_id,
                "task_id": request.task_id,
            },
        }

    @staticmethod
    def from_result(raw: dict) -> ModuleResult:
        status = raw["status"]
        summary = raw.get("final_summary") or raw.get("summary") or status
        payload = raw.get("structured_result") or raw.get("metrics")
        evidence = payload.get("evidence_files", []) if isinstance(payload, dict) else []
        artifacts = [
            ArtifactCandidate(
                kind="experiment_result",
                path=path,
                media_type="application/json",
                summary="experiment evidence",
            )
            for path in evidence
            if isinstance(path, str) and not path.startswith("/") and path.endswith(".json")
        ]
        if status == "completed":
            return ModuleResult(
                status=ModuleStatus.COMPLETED,
                summary=summary,
                payload=payload,
                artifacts=artifacts,
            )
        if status == "completed_with_failures":
            return ModuleResult(
                status=ModuleStatus.COMPLETED_WITH_WARNINGS,
                summary=summary,
                payload=payload,
                artifacts=artifacts,
                warnings=[
                    WarningRecord(
                        code="unverified",
                        message=raw.get("warning", "delivery check reported missing items"),
                    )
                ],
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
        return ModuleResult(
            status=ModuleStatus.FAILED,
            summary=summary,
            error=ModuleError(
                code=ErrorCode.TOOL_FAILED,
                message=raw.get("message", summary),
                retryable=True,
            ),
        )
