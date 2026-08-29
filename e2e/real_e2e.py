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
    DatasetRef,
    ExperimentRunInput,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificVerdict,
    TaskBudget,
    TaskStatus,
    UserAnswer,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_capabilities import ArxivLiteratureBackend, ResourceLayout
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import (
    ArtifactRegistry,
    JsonRunStore,
    LLMWorkflowCompiler,
    ModuleBinding,
    ResearchController,
    WorkflowScheduler,
)
from resagent2_runtime import (
    ComposedContext,
    JsonSessionStore,
    OpenAICompatibleClient,
)
from resagent2_scientific import ScientificAgent

UTIL_PY = 'def add(a, b):\n    return a + b\n'

# A small, real CIFAR-10 training script. The candidate method is a
# Squeeze-and-Excitation (SE) block whose forward pass is deliberately left as
# an identity placeholder; the Coding Agent must implement it. Running without
# --use-se always yields a valid baseline, so the Experiment Agent can always
# produce metrics for both arms.
TRAIN_PY = """\
import argparse
import json
import os

import torch
import torch.nn as nn
import torchvision
import torchvision.transforms as transforms


def _data_root():
    # Datasets are exposed generically by the Experiment Agent. Pick the cifar10
    # entry from the id -> path map, falling back to the shared dataset root.
    mapping = os.environ.get("RESAGENT2_DATASETS_JSON")
    if mapping:
        try:
            datasets = json.loads(mapping)
            if "cifar10" in datasets:
                return datasets["cifar10"]
        except (ValueError, TypeError):
            pass
    return os.environ.get("RESAGENT2_DATASET_ROOT", "./data")


class SELayer(nn.Module):
    def __init__(self, channel, reduction=16):
        super().__init__()
        self.channel = channel
        self.reduction = reduction
        # TODO(candidate): implement squeeze-and-excitation here.
        # Squeeze: global average pool over spatial dims -> (b, c, 1, 1).
        # Excite: fc(channel -> channel/reduction) -> relu ->
        #         fc(channel/reduction -> channel) -> sigmoid -> (b, c, 1, 1).
        # Then rescale x channel-wise by the excitation.
        self.fc = nn.Sequential(
            nn.Linear(channel, channel // reduction),
            nn.ReLU(inplace=True),
            nn.Linear(channel // reduction, channel),
            nn.Sigmoid(),
        )

    def forward(self, x):
        # TODO(candidate): implement the SE forward pass here.
        # Squeeze: b, c, h, w = x.size(); y = avg_pool2d(x, (h, w)).view(b, c)
        # Excite:  y = self.fc(y).view(b, c, 1, 1)
        # Return:  x * y
        raise NotImplementedError("SELayer.forward must be implemented")


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, in_planes, planes, stride=1):
        super().__init__()
        self.conv1 = nn.Conv2d(in_planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.shortcut = nn.Sequential()
        if stride != 1 or in_planes != self.expansion * planes:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_planes, self.expansion * planes, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(self.expansion * planes),
            )

    def forward(self, x):
        out = torch.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        out = torch.relu(out)
        return out


class ResNet(nn.Module):
    def __init__(self, block, num_blocks, use_se=False):
        super().__init__()
        self.in_planes = 64
        self.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.layer1 = self._make_layer(block, 64, num_blocks[0], stride=1)
        self.layer2 = self._make_layer(block, 128, num_blocks[1], stride=2)
        self.layer3 = self._make_layer(block, 256, num_blocks[2], stride=2)
        self.layer4 = self._make_layer(block, 512, num_blocks[3], stride=2)
        self.use_se = use_se
        if use_se:
            self.se = SELayer(512 * block.expansion)
        self.linear = nn.Linear(512 * block.expansion, 10)

    def _make_layer(self, block, planes, num_blocks, stride):
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        for s in strides:
            layers.append(block(self.in_planes, planes, s))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(self, x):
        out = torch.relu(self.bn1(self.conv1(x)))
        out = self.layer1(out)
        out = self.layer2(out)
        out = self.layer3(out)
        out = self.layer4(out)
        if self.use_se:
            # SE operates on the 4D feature map (b, c, h, w) before pooling.
            out = self.se(out)
        out = torch.nn.functional.avg_pool2d(out, 4)
        out = out.view(out.size(0), -1)
        out = self.linear(out)
        return out


def resnet18(use_se=False):
    return ResNet(BasicBlock, [2, 2, 2, 2], use_se=use_se)


def _loader(train):
    if train:
        transform = transforms.Compose(
            [
                transforms.RandomCrop(32, padding=4),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
            ]
        )
    else:
        transform = transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
            ]
        )
    return torch.utils.data.DataLoader(
        torchvision.datasets.CIFAR10(root=_data_root(), train=train, download=False, transform=transform),
        batch_size=128,
        shuffle=train,
        num_workers=2,
    )


def _accuracy(model, loader, device):
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for inputs, targets in loader:
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += predicted.eq(targets).sum().item()
    return round(correct / total, 4)


def _train_arm(epochs, seed, use_se, device, loaders):
    torch.manual_seed(seed)
    model = resnet18(use_se=use_se).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1, momentum=0.9, weight_decay=1e-4)
    train_loader, test_loader = loaders
    for _ in range(epochs):
        model.train()
        for inputs, targets in train_loader:
            inputs, targets = inputs.to(device), targets.to(device)
            optimizer.zero_grad()
            loss = criterion(model(inputs), targets)
            loss.backward()
            optimizer.step()
    return _accuracy(model, test_loader, device)


def train(epochs, seed):
    torch.manual_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    train_loader = _loader(train=True)
    test_loader = _loader(train=False)
    baseline = _train_arm(epochs, seed, use_se=False, device=device, loaders=(train_loader, test_loader))
    candidate = _train_arm(epochs, seed, use_se=True, device=device, loaders=(train_loader, test_loader))
    metrics = {
        "baseline_accuracy": baseline,
        "candidate_accuracy": candidate,
        "epochs": epochs,
        "seed": seed,
    }
    json.dump(metrics, open("metrics.json", "w"))
    print(f"baseline={baseline} candidate={candidate}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    train(parser.parse_args().epochs, parser.parse_args().seed)
"""

# The candidate method must be reflected in the training script. The Coding
# Agent implements the SE forward pass; the Experiment Agent runs baseline and
# candidate arms and freezes metrics.json as evidence.
_REQUIREMENTS_TXT = """\
torch>=2.0
torchvision>=0.15
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
    (repo / "train.py").write_text(TRAIN_PY, encoding="utf-8")
    (repo / "requirements.txt").write_text(_REQUIREMENTS_TXT, encoding="utf-8")
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
        source=WorkspaceSourceKind.LOCAL,
    )


def _coding_agent(session_store) -> NativeCodingAgent:
    return NativeCodingAgent(
        OpenAICompatibleClient(
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
        ),
        store=session_store,
    )


def _experiment_agent(
    session_store, resource_layout: ResourceLayout
) -> NativeExperimentAgent:
    return NativeExperimentAgent(
        OpenAICompatibleClient(
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
        ),
        store=session_store,
        resource_layout=resource_layout,
    )


def _scientific_agent(
    registration_port,
    session_store: JsonSessionStore,
) -> ScientificAgent:
    return ScientificAgent(
        OpenAICompatibleClient(
            model=_MODEL,
            api_base=_API_BASE,
            api_key_env=_API_KEY_ENV,
        ),
        literature_backend=ArxivLiteratureBackend(),
        registration_port=registration_port,
        store=session_store,
    )


class _ScientificArtifactRegistration:
    """Record a Scientific Tool artifact into the run's artifact index.

    ``ScientificAgent`` calls ``register_scientific(candidate, run_id,
    session_id)`` on the injected ``ArtifactRegistrationPort`` (CONTRACTS
    §20.12); ``ArtifactRegistry.register_scientific`` only freezes the file. The
    controller's observed-review reads ``run.artifacts``, so this adapter also
    stores the returned ArtifactRef in the run snapshot, otherwise the
    literature artifact would be rejected as unknown.
    """

    def __init__(self, registry: ArtifactRegistry, store) -> None:
        self._registry = registry
        self._store = store

    def register_scientific(self, candidate, *, run_id, session_id):
        artifact = self._registry.register_scientific(
            candidate, run_id=run_id, session_id=session_id
        )
        run = self._store.load(run_id)
        run.artifacts[artifact.id] = artifact
        self._store.save(run)
        return artifact


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
            text=prompt,
            included_sections=[],
            omitted_sections=[],
            estimated_tokens=0,
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


def _owner_for(registry: CapabilityRegistry, capability: Capability) -> AgentOwner:
    """Single source of truth for capability ownership (CONTRACTS §20.10.2)."""
    for definition in registry.definitions:
        if definition.capability == capability:
            return definition.owner
    raise KeyError(f"no owner registered for capability {capability.value}")


def _build_controller(workdir: Path, repo: Path | None):
    """Assemble the registry, scheduler and controller for one E2E scenario."""
    registry = _registry()
    run_store = JsonRunStore(workdir / "state")
    resource_layout = ResourceLayout.from_env(data_root=workdir / "data")
    workspaces = {}
    if repo is not None:
        workspaces["ws_main"] = WorkspaceSpec(
            workspace_id="ws_main",
            source_kind=WorkspaceSourceKind.LOCAL,
            location=str(repo),
        )
    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=_owner_for(registry, Capability.CODE_MODIFY),
                port=_coding_agent(JsonSessionStore(workdir / "coding_sessions")),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=_owner_for(registry, Capability.EXPERIMENT_RUN),
                port=_experiment_agent(
                    JsonSessionStore(workdir / "experiment_sessions"), resource_layout
                ),
            ),
        },
        store=run_store,
        artifact_root=workdir / "artifacts",
        data_root=workdir / "data",
        workspaces=workspaces,
    )
    controller = ResearchController(
        scientific_port=_scientific_agent(
            _ScientificArtifactRegistration(scheduler.artifact_registry, run_store),
            JsonSessionStore(workdir / "scientific_sessions"),
        ),
        compiler=LLMWorkflowCompiler(
            _CompilerClient(model=_MODEL, api_base=_API_BASE, api_key_env=_API_KEY_ENV)
        ),
        scheduler=scheduler,
        registry=registry,
    )
    return controller, run_store


_BUGGY_TRAIN_PY = """\
import json


def main():
    # BUG: references `accuracy` before it is assigned (a clear, fixable runtime error).
    result = accuracy + 0.1
    json.dump({"accuracy": result}, open("metrics.json", "w"))


if __name__ == "__main__":
    main()
"""


def _repair_repo(workdir: Path) -> Path:
    repo = workdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "train.py").write_text(_BUGGY_TRAIN_PY, encoding="utf-8")
    (repo / "requirements.txt").write_text("", encoding="utf-8")
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "e2e@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "e2e"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _real_e2e_succeeded(run) -> bool:
    """Require completed tasks, task-owned evidence and a final scientific opinion."""
    if run.status != RunStatus.COMPLETED or run.final_opinion is None:
        return False
    if run.final_report_artifact_id is None:
        return False
    if run.workflow is None:
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
        goal="Implement the Squeeze-and-Excitation forward pass in train.py",
        inputs=CodeModifyInput(
            instructions="Implement SELayer.forward in train.py (it raises NotImplementedError)",
        ),
        budget=TaskBudget(max_steps=24, max_llm_calls=40, timeout_seconds=900),
        workspace=_grant(repo),
        output_dir=str(workdir / "out"),
    )
    return _coding_agent(JsonSessionStore(workdir / "sessions")).invoke(request)


def run_experiment(workdir: Path) -> ModuleResult:
    repo = _repo(workdir)
    resource_layout = ResourceLayout.from_env(data_root=workdir / "data")
    request = ModuleTaskRequest(
        run_id="run_real",
        task_id="task_experiment",
        attempt_number=1,
        capability=Capability.EXPERIMENT_RUN,
        goal="Run train.py and record baseline and candidate accuracy",
        inputs=ExperimentRunInput(
            instructions=(
                "Run train.py (it trains both the baseline and the SE candidate "
                "and writes metrics.json with baseline_accuracy and "
                "candidate_accuracy). Record those accuracies."
            ),
            expected_metrics=["accuracy"],
            expected_artifacts=["metrics.json"],
            dataset_refs=[DatasetRef(dataset_id="cifar10", relative_path="cifar10")],
        ),
        budget=TaskBudget(max_steps=30, max_llm_calls=60, timeout_seconds=1800),
        workspace=_grant(repo),
        output_dir=str(workdir / "out"),
    )
    return _experiment_agent(
        JsonSessionStore(workdir / "sessions"), resource_layout
    ).invoke(request)


def run_full(workdir: Path) -> bool:
    repo = _repo(workdir)
    request = ResearchRequest(
        goal=(
            "On CIFAR-10, compare the test accuracy of two ResNet18 variants. "
            "The baseline is the standard ResNet18 already implemented in train.py. "
            "The candidate adds a Squeeze-and-Excitation (SE) channel-attention "
            "block after the final conv layer (on the 4D feature map, before "
            "global pooling): global average pool over spatial dims, then "
            "fc(channel -> channel/16) -> relu -> fc(channel/16 -> channel) -> "
            "sigmoid, rescaling channels element-wise. The SELayer.forward in "
            "train.py currently raises NotImplementedError and must be "
            "implemented (the SE arm crashes until it is). train.py already "
            "runs both arms (baseline and SE) and "
            "writes metrics.json with baseline_accuracy and candidate_accuracy; "
            "the training protocol (SGD, epochs, seed=0) is fixed and must not "
            "be changed. Conclude whether the SE block improves accuracy over "
            "the baseline."
        ),
        dataset_refs=[DatasetRef(dataset_id="cifar10", relative_path="cifar10")],
        budget=RunBudget(
            max_tasks=2, max_attempts_per_task=2, max_llm_calls=200, timeout_seconds=3600
        ),
    )
    controller, _ = _build_controller(workdir, repo)
    run = controller.create_run("run_full_real", request)
    tasks = run.workflow.tasks if run.workflow is not None else []
    for task in tasks:
        attempts = ", ".join(f"{a.number}:{a.status.value}" for a in task.attempts)
        print(f"{task.id} [{task.capability.value}] {task.status.value} attempts={attempts}")
    print(f"run status={run.status.value} artifacts={len(run.artifacts)}")
    return _real_e2e_succeeded(run)


def _direct_succeeded(run) -> bool:
    """Scenario 1 acceptance: completed, inconclusive, no task graph, final report."""
    return (
        run.status == RunStatus.COMPLETED
        and run.final_opinion is not None
        and run.final_opinion.verdict == ScientificVerdict.INCONCLUSIVE
        and run.final_report_artifact_id is not None
        and (run.workflow is None or not run.workflow.tasks)
    )


def _repair_succeeded(run) -> bool:
    """Scenario 3 acceptance: completed with a preserved failed attempt + recovery."""
    tasks = run.workflow.tasks if run.workflow is not None else []
    had_failure = any(
        any(a.status.value == "failed" for a in task.attempts) for task in tasks
    )
    return (
        run.status == RunStatus.COMPLETED
        and run.final_opinion is not None
        and run.final_report_artifact_id is not None
        and had_failure
        and len(run.work_requests) >= 2
    )


def _ask_start_succeeded(run) -> bool:
    """Scenario 4a acceptance: paused with a persisted pending question."""
    return run.status == RunStatus.PAUSED and run.pending_question is not None


def _ask_resume_succeeded(run) -> bool:
    """Scenario 4b acceptance: resumed and completed with a final opinion."""
    return run.status == RunStatus.COMPLETED and run.final_opinion is not None


def _literature_succeeded(run) -> bool:
    """Scenario 5 acceptance: opinion cites a registered literature artifact."""
    cited = set(run.final_opinion.evidence_artifact_ids) if run.final_opinion else set()
    literature_ids = [
        a.id for a in run.artifacts.values() if a.kind == "literature_search"
    ]
    return (
        run.status == RunStatus.COMPLETED
        and run.final_opinion is not None
        and run.final_report_artifact_id is not None
        and bool(literature_ids)
        and any(artifact_id in cited for artifact_id in literature_ids)
    )


def run_direct(workdir: Path) -> bool:
    """Scenario 1: the Scientific Agent concludes inconclusive without any work."""
    controller, _ = _build_controller(workdir, None)
    request = ResearchRequest(
        goal=(
            "Determine whether the observed CIFAR-10 accuracy improvement is a "
            "causal effect of the SE block or merely correlation."
        ),
        constraints=[
            "Do not request experiments or any additional work; conclude from "
            "the available evidence only."
        ],
        budget=RunBudget(
            max_tasks=1, max_attempts_per_task=1, max_llm_calls=30, timeout_seconds=600
        ),
    )
    run = controller.create_run("run_direct", request)
    print(f"run status={run.status.value}")
    if run.final_opinion is not None:
        print(f"verdict={run.final_opinion.verdict.value}")
    return _direct_succeeded(run)


def run_repair(workdir: Path) -> bool:
    """Scenario 3: a failed experiment is diagnosed, fixed and rerun."""
    repo = _repair_repo(workdir)
    request = ResearchRequest(
        goal=(
            "Run train.py to measure the accuracy it produces. If the run "
            "fails, diagnose the error, fix the code, and rerun to obtain the "
            "accuracy."
        ),
        budget=RunBudget(
            max_tasks=4, max_attempts_per_task=3, max_llm_calls=200, timeout_seconds=3600
        ),
    )
    controller, _ = _build_controller(workdir, repo)
    run = controller.create_run("run_repair", request)
    tasks = run.workflow.tasks if run.workflow is not None else []
    for task in tasks:
        attempts = ", ".join(f"{a.number}:{a.status.value}" for a in task.attempts)
        print(f"{task.id} [{task.capability.value}] {task.status.value} attempts={attempts}")
    print(f"run status={run.status.value} work_requests={len(run.work_requests)}")
    return _repair_succeeded(run)


def run_ask_start(workdir: Path) -> bool:
    """Scenario 4a: pause on a Scientific question and persist the session."""
    repo = _repo(workdir)
    request = ResearchRequest(
        goal=(
            "Compare two candidate methods on CIFAR-10. State which accuracy "
            "metric should be reported before running."
        ),
        dataset_refs=[DatasetRef(dataset_id="cifar10", relative_path="cifar10")],
        budget=RunBudget(
            max_tasks=2, max_attempts_per_task=2, max_llm_calls=40, timeout_seconds=600
        ),
    )
    controller, _ = _build_controller(workdir, repo)
    run = controller.create_run("run_ask", request)
    print(f"run status={run.status.value}")
    if run.pending_question is not None:
        print(f"pending_question={run.pending_question.text}")
        print(f"requested_fields={run.pending_question.requested_fields}")
    return _ask_start_succeeded(run)


def run_ask_resume(workdir: Path, answer_text: str) -> bool:
    """Scenario 4b: resume the paused run in a fresh process and complete it."""
    repo = _repo(workdir)
    controller, run_store = _build_controller(workdir, repo)
    run = run_store.load("run_ask")
    question = run.pending_question
    if question is None:
        print("no pending question to answer")
        return False
    answer = UserAnswer(
        question_id=question.id,
        values={field: answer_text for field in question.requested_fields},
    )
    run = controller.answer_question("run_ask", answer)
    print(f"run status={run.status.value}")
    return _ask_resume_succeeded(run)


def run_literature(workdir: Path) -> bool:
    """Scenario 5: gather literature evidence and cite it in the opinion."""
    controller, _ = _build_controller(workdir, None)
    request = ResearchRequest(
        goal=(
            "What does the literature say about Squeeze-and-Excitation networks "
            "improving image classification accuracy? Summarize the evidence "
            "from the papers you retrieve."
        ),
        budget=RunBudget(
            max_tasks=1, max_attempts_per_task=1, max_llm_calls=60, timeout_seconds=900
        ),
    )
    run = controller.create_run("run_literature", request)
    print(f"run status={run.status.value} artifacts={len(run.artifacts)}")
    return _literature_succeeded(run)


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
    elif stage in {"full", "code-experiment"}:
        sys.exit(0 if run_full(workdir) else 1)
    elif stage == "direct":
        sys.exit(0 if run_direct(workdir) else 1)
    elif stage == "repair":
        sys.exit(0 if run_repair(workdir) else 1)
    elif stage == "ask-start":
        sys.exit(0 if run_ask_start(workdir) else 1)
    elif stage == "ask-resume":
        answer_text = sys.argv[2] if len(sys.argv) > 2 else ""
        sys.exit(0 if run_ask_resume(workdir, answer_text) else 1)
    elif stage == "literature":
        sys.exit(0 if run_literature(workdir) else 1)
    else:
        raise SystemExit(f"unknown stage: {stage}")


if __name__ == "__main__":
    main()
