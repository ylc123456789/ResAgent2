"""Real closed-loop test with native Coding/Experiment/Scientific agents.

Calls the Phase 5 native Coding Agent, the Phase 6 native Experiment Agent and
the Phase 7 native Scientific Agent through the ResearchController, using the
real DeepSeek LLM. The experiment environment is content-addressed; set
RESAGENT2_RESOURCE_ROOT to a stable directory to reuse conda envs across runs
(RESAGENT2_CONDA_EXE overrides conda).

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
    CapabilityDefinition,
    CapabilityRegistry,
    CodeModifyInput,
    ExperimentRunInput,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskBudget,
    TaskStatus,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import (
    JsonRunStore,
    LLMWorkflowCompiler,
    ModuleBinding,
    ResearchController,
    WorkflowScheduler,
)
from resagent2_runtime import (
    ComposedContext,
    OpenAICompatibleClient,
)
from resagent2_scientific import ScientificAgent

UTIL_PY = 'def add(a, b):\n    return a + b\n'

TRAIN_PY = """\
import argparse
import json
import random


def train(epochs):
    random.seed(0)
    total = 2000
    correct = 0
    for _ in range(total):
        features = [random.uniform(-1, 1) for _ in range(20)]
        label = 1 if sum(features) > 0 else 0
        prediction = 1 if sum(features) > 0.05 else 0
        if prediction == label:
            correct += 1
    accuracy = correct / total
    json.dump({"accuracy": round(accuracy, 4), "epochs": epochs}, open("metrics.json", "w"))
    print(f"accuracy={accuracy:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=1)
    train(parser.parse_args().epochs)
"""

_EXPECTED_TASK_CAPABILITIES = {
    Capability.CODE_MODIFY,
    Capability.EXPERIMENT_RUN,
}

_MODEL = "deepseek-chat"
_API_BASE = "https://api.deepseek.com/v1"
_API_KEY_ENV = "DEEPSEEK_API_KEY"


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


def _coding_agent() -> NativeCodingAgent:
    return NativeCodingAgent(
        OpenAICompatibleClient(
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
        )
    )


def _experiment_agent() -> NativeExperimentAgent:
    return NativeExperimentAgent(
        OpenAICompatibleClient(
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
        )
    )


def _scientific_agent() -> ScientificAgent:
    return ScientificAgent(
        OpenAICompatibleClient(
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
        )
    )


class _CompilerClient:
    """Adapt the runtime OpenAI client to the orchestrator CompilerLLM seam.

    The runtime client consumes a ``ComposedContext``; the compiler supplies a
    plain prompt string. This adapter bridges the two without importing the
    runtime into the orchestrator.
    """

    def __init__(self, *, model: str, api_base: str, api_key_env: str) -> None:
        self._client = OpenAICompatibleClient(
            model=model, api_base=api_base, api_key_env=api_key_env
        )

    def next_action(self, prompt: str, action_type):
        context = ComposedContext(
            text=prompt, included_sections=[], omitted_sections=[]
        )
        return self._client.next_action(context, action_type)


def _registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        definitions=[
            CapabilityDefinition(
                capability=Capability.CODE_MODIFY,
                owner=AgentOwner.CODING,
                request_model="CodeModifyInput",
                result_model="CodeModifyResult",
                permission_policy="read_write_workspace",
                completion_evidence=["code_change"],
            ),
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                request_model="ExperimentRunInput",
                result_model="ExperimentResult",
                permission_policy="read_write_workspace",
                completion_evidence=["experiment_result"],
            ),
        ]
    )


def _real_e2e_succeeded(run) -> bool:
    """Require completed tasks, task-owned evidence and a final scientific opinion."""
    if run.status != RunStatus.COMPLETED or run.final_opinion is None:
        return False
    if run.final_report_artifact_id is None:
        return False
    tasks = {task.capability: task for task in run.workflow.tasks}
    if (
        len(run.workflow.tasks) != len(_EXPECTED_TASK_CAPABILITIES)
        or set(tasks) != _EXPECTED_TASK_CAPABILITIES
    ):
        return False
    if any(
        task.status != TaskStatus.COMPLETED or not task.attempts
        for task in tasks.values()
    ):
        return False

    artifacts = list(run.artifacts.values())

    def has_artifact(capability: Capability, kind: str) -> bool:
        task_id = tasks[capability].id
        return any(
            artifact.kind == kind and artifact.task_id == task_id
            for artifact in artifacts
        )

    if not has_artifact(Capability.EXPERIMENT_RUN, "experiment_result"):
        return False
    return has_artifact(Capability.CODE_MODIFY, "code_change")


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
    return _coding_agent().invoke(request)


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
            external_repo_path=str(repo),
        ),
        budget=TaskBudget(max_steps=30, max_llm_calls=60, timeout_seconds=1800),
        workspace=_grant(repo),
    )
    return _experiment_agent().invoke(request)


def run_full(workdir: Path) -> bool:
    repo = _repo(workdir)
    request = ResearchRequest(
        goal="Determine whether the method improves accuracy",
        budget=RunBudget(
            max_tasks=6, max_attempts_per_task=2, max_llm_calls=200, timeout_seconds=3600
        ),
    )
    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=AgentOwner.CODING,
                port=_coding_agent(),
                workspace=_grant(repo),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=_experiment_agent(),
                workspace=_grant(repo),
            ),
        },
        store=JsonRunStore(workdir / "state"),
        artifact_root=workdir / "artifacts",
    )
    controller = ResearchController(
        scientific_port=_scientific_agent(),
        compiler=LLMWorkflowCompiler(
            _CompilerClient(model=_MODEL, api_base=_API_BASE, api_key_env=_API_KEY_ENV)
        ),
        scheduler=scheduler,
        registry=_registry(),
    )
    run = controller.create_run("run_full_real", request)
    for task in run.workflow.tasks:
        attempts = ", ".join(f"{a.number}:{a.status.value}" for a in task.attempts)
        print(f"{task.id} [{task.capability.value}] {task.status.value} attempts={attempts}")
    print(f"run status={run.status.value} artifacts={len(run.artifacts)}")
    return _real_e2e_succeeded(run)


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "full"
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
