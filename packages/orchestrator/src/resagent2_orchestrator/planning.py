"""Control-plane Planning Port and a deterministic implementation."""

from __future__ import annotations

from typing import Protocol

from resagent2_contracts import (
    Capability,
    CodeModifyInput,
    ExperimentRunInput,
    ResearchRequest,
    ScientificAnalyzeInput,
    SuccessCriterion,
    TaskProposal,
    VerificationMode,
    WorkflowProposal,
)


class PlanningPort(Protocol):
    """Control-plane seam that turns a ResearchRequest into a WorkflowProposal."""

    def propose(self, request: ResearchRequest) -> WorkflowProposal:
        """Produce a task graph before any WorkflowTask is scheduled."""


class DeterministicPlanningPort:
    """Return a fixed golden proposal: code -> experiment -> analyze.

    The plan is produced by the control plane and therefore never appears as a
    ``scientific_plan`` WorkflowTask; only task-plane capabilities are emitted.
    """

    def propose(self, request: ResearchRequest) -> WorkflowProposal:
        return WorkflowProposal(
            summary="Deterministic golden loop",
            scientific_rationale="Local mock E2E exercises the full closed loop",
            tasks=[
                TaskProposal(
                    id="task_code",
                    capability=Capability.CODE_MODIFY,
                    goal="Add a docstring to the add() function in util.py",
                    rationale="Produce a verified code change",
                    depends_on=[],
                    required=True,
                    inputs=CodeModifyInput(
                        instructions="Add a docstring to the add() function in util.py",
                        verification_commands=[
                            "python -c \"import util; assert util.add(2, 3) == 5\""
                        ],
                    ),
                    success_criteria=[
                        SuccessCriterion(
                            description="A code change is produced",
                            verification=VerificationMode.AUTOMATIC,
                            evidence_key="code_patch",
                        )
                    ],
                ),
                TaskProposal(
                    id="task_experiment",
                    capability=Capability.EXPERIMENT_RUN,
                    goal="Run the experiment and record metrics",
                    rationale="Produce evidence for analysis",
                    depends_on=["task_code"],
                    required=True,
                    inputs=ExperimentRunInput(
                        instructions="Run train.py and record accuracy from metrics.json",
                        expected_metrics=["accuracy"],
                        expected_artifacts=["metrics.json"],
                    ),
                    success_criteria=[
                        SuccessCriterion(
                            description="Metrics are produced",
                            verification=VerificationMode.AUTOMATIC,
                            evidence_key="metrics",
                        )
                    ],
                ),
                TaskProposal(
                    id="task_analyze",
                    capability=Capability.SCIENTIFIC_ANALYZE,
                    goal=(
                        "Determine whether the recorded accuracy supports the hypothesis "
                        "that the method beats a ~0.5 random-chance baseline on the synthetic benchmark."
                    ),
                    rationale="Form a conclusion from registered artifacts",
                    depends_on=["task_experiment"],
                    required=True,
                    inputs=ScientificAnalyzeInput(
                        question=(
                            "The synthetic benchmark has a ~0.5 random-chance baseline. "
                            "Does the recorded accuracy support the hypothesis that the method "
                            "improves over baseline?"
                        ),
                        evidence_artifact_ids=[],
                    ),
                    success_criteria=[
                        SuccessCriterion(
                            description="A conclusion is formed",
                            verification=VerificationMode.AUTOMATIC,
                            evidence_key="conclusion",
                        )
                    ],
                ),
            ],
        )
