"""Adapter for the old ExpAgent module (scientific analysis).

DELETION CONDITION: delete once Phase 7 ``Scientific Agent vNext`` replaces it.
This adapter only translates request/result shapes; it must not copy ExpAgent's
scientific reasoning or planning logic.
"""

from __future__ import annotations

import importlib
import json
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

# ResAgent2 ArtifactRef.kind -> ExpAgent ArtifactRef.type
_KIND_TO_TYPE = {
    "experiment_result": "metric_summary",
    "code_change": "code_patch",
    "run_log": "run_log",
    "scientific_decision": "other",
}


def _uri_to_path(uri: str) -> str | None:
    """Turn a ResAgent2 file:// URI into a filesystem path ExpAgent can read."""
    if uri.startswith("file://"):
        return uri.removeprefix("file://")
    return None


def _session_status(run_dir: Path) -> str | None:
    card = run_dir / "session.yaml"
    if not card.is_file():
        return None
    for line in card.read_text(encoding="utf-8").splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    return None


class LegacyScientificAnalyzeAdapter:
    """Map ModuleTaskRequest <-> ExpAgent AdvisorContext / ScientificDecision."""

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        root = os.environ.get("EXPAGENT_PATH", "/root/autodl-tmp/projects/ExpAgent")
        src = str(Path(root) / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        models = importlib.import_module("experiment_designer.models")
        agent = importlib.import_module("experiment_designer.agent")

        base = Path(request.workspace.root) if request.workspace else Path.cwd()
        run_dir = base / f"{request.task_id}_attempt{request.attempt_number}"
        artifacts = [
            {
                "id": artifact.id,
                "type": _KIND_TO_TYPE.get(artifact.kind, "other"),
                "path": _uri_to_path(artifact.uri),
                "summary": artifact.summary,
            }
            for artifact in request.input_artifacts
        ]
        ctx = models.AdvisorContext(situation=request.goal, artifacts=artifacts)
        decision, _steps = agent.advise(
            ctx,
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
            run_dir=run_dir,
        )
        if _session_status(run_dir) == "failed":
            return ModuleResult(
                status=ModuleStatus.FAILED,
                summary="scientific analysis failed",
                error=ModuleError(
                    code=ErrorCode.TOOL_FAILED,
                    message="ExpAgent session failed (see session.yaml)",
                    retryable=True,
                ),
            )
        result = self.from_result(decision.model_dump(mode="json"))
        if result.status == ModuleStatus.COMPLETED:
            conclusion = f"conclusion_{request.task_id}_{request.attempt_number}.json"
            (base / conclusion).write_text(
                json.dumps(decision.model_dump(mode="json")), encoding="utf-8"
            )
            result = result.model_copy(
                update={
                    "artifacts": [
                        ArtifactCandidate(
                            kind="scientific_decision",
                            path=conclusion,
                            media_type="application/json",
                            summary=result.summary,
                        )
                    ]
                }
            )
        return result

    @staticmethod
    def to_spec(request: ModuleTaskRequest) -> dict:
        return {
            "situation": request.goal,
            "artifacts": [artifact.model_dump(mode="json") for artifact in request.input_artifacts],
        }

    @staticmethod
    def from_result(raw: dict) -> ModuleResult:
        summary = raw.get("summary") or "analysis complete"
        needs = raw.get("needs_user_input") or []
        if needs:
            return ModuleResult(
                status=ModuleStatus.NEEDS_USER_INPUT,
                summary=summary,
                question=QuestionDraft(
                    text=needs[0],
                    reason="scientific analysis requires user input",
                ),
            )
        return ModuleResult(
            status=ModuleStatus.COMPLETED,
            summary=summary,
            payload={
                "conclusion": raw.get("conclusion"),
                "confidence": raw.get("confidence"),
            },
        )
