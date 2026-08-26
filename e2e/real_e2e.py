"""Real closed-loop test: drive the legacy adapters against the OLD modules.

Unlike mock_e2e, this calls the real CodingAgent / reproagent / ExpAgent (via
their in-process Python APIs) and the real DeepSeek LLM. Module roots are
resolved from CODINGAGENT_PATH / REPROAGENT_PATH / EXPAGENT_PATH (defaulting to
the AutoDL layout); REPROAGENT_ENV_NAME may point at an existing torch env to
skip conda env creation.

Stages: ``python -m e2e.real_e2e code|experiment|full``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeModifyInput,
    ExperimentRunInput,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificAnalyzeInput,
    TaskBudget,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_orchestrator import (
    DeterministicPlanningPort,
    JsonRunStore,
    ModuleBinding,
    WorkflowScheduler,
)
from resagent2_orchestrator.adapters import (
    LegacyCodingAdapter,
    LegacyExperimentAdapter,
    LegacyScientificAnalyzeAdapter,
)

UTIL_PY = 'def add(a, b):\n    return a + b\n'

TRAIN_PY = """\
import argparse, json
import torch, torch.nn as nn, torch.optim as optim


def train(epochs):
    torch.manual_seed(0)
    X = torch.randn(2000, 20)
    y = (X.sum(dim=1) > 0).float().unsqueeze(1)
    model = nn.Sequential(nn.Linear(20, 16), nn.ReLU(), nn.Linear(16, 1), nn.Sigmoid())
    opt = optim.Adam(model.parameters(), lr=0.01)
    loss = nn.BCELoss()
    for _ in range(epochs):
        opt.zero_grad()
        loss(model(X), y).backward()
        opt.step()
    acc = ((model(X) > 0.5).float() == y).float().mean().item()
    json.dump({"accuracy": round(acc, 4), "epochs": epochs}, open("metrics.json", "w"))
    print(f"accuracy={acc:.4f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--epochs", type=int, default=1)
    train(p.parse_args().epochs)
"""


def _repo(workdir: Path) -> Path:
    repo = workdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "util.py").write_text(UTIL_PY, encoding="utf-8")
    (repo / "train.py").write_text(TRAIN_PY, encoding="utf-8")
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "e2e@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "e2e"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _grant(repo: Path) -> WorkspaceGrant:
    return WorkspaceGrant(
        root=str(repo),
        mode=WorkspaceMode.READ_WRITE,
        allowed_paths=["."],
        source=WorkspaceSource.EXISTING,
    )


def run_code(workdir: Path) -> ModuleResult:
    repo = _repo(workdir)
    request = ModuleTaskRequest(
        run_id="run_real",
        task_id="task_code",
        attempt_number=1,
        capability=Capability.CODE_MODIFY,
        goal="Add a docstring to the add() function in util.py",
        inputs=CodeModifyInput(
            instructions="Add a docstring to the add() function in util.py",
            verification_commands=["python -c \"import util; assert util.add(2, 3) == 5\""],
        ),
        budget=TaskBudget(max_steps=24, max_llm_calls=40, timeout_seconds=900),
        workspace=_grant(repo),
    )
    return LegacyCodingAdapter().invoke(request)


def run_experiment(workdir: Path) -> ModuleResult:
    repo = _repo(workdir)
    request = ModuleTaskRequest(
        run_id="run_real",
        task_id="task_experiment",
        attempt_number=1,
        capability=Capability.EXPERIMENT_RUN,
        goal="Run train.py with 2 epochs and record the accuracy from metrics.json",
        inputs=ExperimentRunInput(
            instructions="Run train.py with 2 epochs and record accuracy from metrics.json",
            expected_metrics=["accuracy"],
            expected_artifacts=["metrics.json"],
        ),
        budget=TaskBudget(max_steps=30, max_llm_calls=60, timeout_seconds=1800),
        workspace=_grant(repo),
    )
    return LegacyExperimentAdapter().invoke(request)


def run_full(workdir: Path) -> bool:
    repo = _repo(workdir)
    request = ResearchRequest(
        goal="Determine whether the method improves accuracy",
        budget=RunBudget(
            max_tasks=6, max_attempts_per_task=2, max_llm_calls=200, timeout_seconds=3600
        ),
    )
    proposal = DeterministicPlanningPort().propose(request)
    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=AgentOwner.CODING,
                port=LegacyCodingAdapter(),
                workspace=_grant(repo),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=LegacyExperimentAdapter(),
                workspace=_grant(repo),
            ),
            Capability.SCIENTIFIC_ANALYZE: ModuleBinding(
                owner=AgentOwner.SCIENTIFIC,
                port=LegacyScientificAnalyzeAdapter(),
                workspace=_grant(repo),
            ),
        },
        store=JsonRunStore(workdir / "state"),
        artifact_root=workdir / "artifacts",
    )
    run = scheduler.create_run("run_full_real", request, proposal)
    run = scheduler.run_until_stable("run_full_real")
    for task in run.workflow.tasks:
        attempts = ", ".join(f"{a.number}:{a.status.value}" for a in task.attempts)
        print(f"{task.id} [{task.capability.value}] {task.status.value} attempts={attempts}")
    print(f"run status={run.status.value} artifacts={len(run.artifacts)}")
    return run.status == RunStatus.COMPLETED and len(run.artifacts) >= 3


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    workdir = Path(os.environ.get("REAL_E2E_WORKDIR", tempfile.mkdtemp(prefix="resagent2-real-")))
    workdir.mkdir(parents=True, exist_ok=True)
    if stage == "code":
        result = run_code(workdir)
        print(f"status={result.status.value}")
        print(f"summary={result.summary}")
        print(f"payload={result.payload}")
        sys.exit(0 if result.status == ModuleStatus.COMPLETED else 1)
    elif stage == "experiment":
        result = run_experiment(workdir)
        print(f"status={result.status.value}")
        print(f"summary={result.summary}")
        print(f"payload={result.payload}")
        sys.exit(0 if result.status == ModuleStatus.COMPLETED else 1)
    elif stage == "full":
        sys.exit(0 if run_full(workdir) else 1)
    else:
        raise SystemExit(f"unknown stage: {stage}")


if __name__ == "__main__":
    main()
