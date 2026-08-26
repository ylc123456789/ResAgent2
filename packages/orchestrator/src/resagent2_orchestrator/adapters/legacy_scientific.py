"""Adapter for the old ExpAgent module (scientific analysis).

DELETION CONDITION: delete once Phase 7 ``Scientific Agent vNext`` replaces it.
This adapter only translates request/result shapes; it must not copy ExpAgent's
scientific reasoning or planning logic.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from resagent2_contracts import (
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    QuestionDraft,
)

_MODEL = "deepseek-chat"
_API_BASE = "https://api.deepseek.com/v1"
_API_KEY_ENV = "DEEPSEEK_API_KEY"


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
        ctx = models.AdvisorContext(
            situation=request.goal,
            artifacts=[a.model_dump(mode="json") for a in request.input_artifacts],
        )
        decision, _steps = agent.advise(
            ctx,
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
            run_dir=run_dir,
        )
        return self.from_result(decision.model_dump(mode="json"))

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
