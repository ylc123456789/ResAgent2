"""Real closed-loop test: drive the legacy adapters against the OLD modules.

Unlike mock_e2e, this calls the real CodingAgent / reproagent / ExpAgent (via
their in-process Python APIs) and the real DeepSeek LLM. Set the module roots
with CODINGAGENT_PATH / REPROAGENT_PATH / EXPAGENT_PATH if they are not at the
default AutoDL locations.

Run a single stage with ``python -m e2e.real_e2e analyze`` (fastest, no
conda/GPU) or ``python -m e2e.real_e2e full``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from resagent2_contracts import (
    Capability,
    ModuleResult,
    ModuleTaskRequest,
    ScientificAnalyzeInput,
    TaskBudget,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_orchestrator.adapters import LegacyScientificAnalyzeAdapter


def _analyze_request(workspace: Path) -> ModuleTaskRequest:
    return ModuleTaskRequest(
        run_id="run_real",
        task_id="task_analyze",
        attempt_number=1,
        capability=Capability.SCIENTIFIC_ANALYZE,
        goal=(
            "A minimal method was trained on MNIST and reached accuracy 0.90 "
            "versus a 0.88 baseline. Does the evidence support the hypothesis "
            "that the method improves accuracy?"
        ),
        inputs=ScientificAnalyzeInput(
            question="Does the evidence support the hypothesis?",
            evidence_artifact_ids=[],
        ),
        budget=TaskBudget(max_steps=8, max_llm_calls=20, timeout_seconds=600),
        workspace=WorkspaceGrant(
            root=str(workspace),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSource.EXISTING,
        ),
    )


def run_analyze(workdir: Path) -> ModuleResult:
    workspace = workdir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    adapter = LegacyScientificAnalyzeAdapter()
    return adapter.invoke(_analyze_request(workspace))


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    workdir = Path("/root/autodl-tmp/resagent2-real-e2e")
    if stage == "analyze":
        result = run_analyze(workdir)
        print(f"status={result.status.value}")
        print(f"summary={result.summary}")
        print(f"payload={result.payload}")
    else:
        raise SystemExit(f"unknown stage: {stage}")


if __name__ == "__main__":
    main()
