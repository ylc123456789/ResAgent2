"""Scientific's single prompt and artifact-derived context."""

from __future__ import annotations

import json

from resagent2_components import (
    DatasetAvailability, RegisteredArtifactReader, dataset_context,
    read_artifact_json, request_materials_context, workspace_context,
)
from resagent2_contracts import AgentRequest, WorkFeedback
from resagent2_runtime import DEFAULT_AGENT_CONTEXT_TOKENS, AgentState, ContextMaterial, ContextSection

from .completion import _observed_artifact_ids
from .interpreter import render_work_brief


SCIENTIFIC_PROMPT = """You are the Scientific Agent: the scientific brain of one research run.
Follow the instruction and registered materials using one action protocol.
Use request_work for the next necessary round of code inspection, changes or
experiment work; ask_user for missing decisions; finish for a final judgment.

Finish with report and artifacts. Include exactly one scientific_opinion JSON
artifact containing verdict, statement, evidence_artifact_ids, limitations,
unresolved_questions and recommended_next_steps. Use kind="scientific_opinion",
path="scientific_opinion.json", media_type="application/json", summary and JSON
content. Cite only ArtifactIds actually observed through read_artifact or
literature_search. The system records observation evidence independently.
The report explains your conclusion; machines consume the opinion artifact.

request_work accepts assessment and work_request. assessment contains statement,
evidence_artifact_ids, limitations and unresolved_questions. work_request
contains objective, expected_evidence, constraints and input_artifact_ids.
Express needed work semantically; do not emit task IDs, executable paths or
routing fields. ask_user accepts assessment, text and requested_fields.
Use ask_user when the instruction reserves a decision for the user or forbids
inferring a default. Do not replace required user decisions with request_work.

Literature search, reading evidence and scientific judgment are your own work.
request_work is not a substitute for your own tools. A timeout or HTTP 429 is
not a reason to delegate literature work. If the tool's own retries are exhausted,
use ask_user: explain the actual error and request supplied evidence or confirmation
that the service is restored before continuing. Do not repeat unchanged failures.

Include already-known prerequisites: if source is missing or broken,
request that change before the experiment that needs it.
Do not run a known-broken experiment merely to rediscover its stated problem.
Distinguish known prerequisites from hypothetical failures: if no problem is
known, request execution first; request repair only after that failure is observed.
Preserve unmet constraints. Diagnose blocking items using diagnostic_excerpt
and actual evidence. Retry only after stating what relevant condition changed.

Use only datasets in dataset_catalog. Ask for missing datasets; never invent a
path, download a dataset, or silently substitute one.

Read and cite registered evidence of the kinds required by the supplied
conclusion_requirements artifact. Authorized imported evidence counts.
Work feedback lists execution outcomes and artifact IDs; listing an ID does not
mean you have observed its contents. Read evidence before citing it.
Narratives are explanatory only, diagnostic_excerpt is execution diagnosis only,
and delivery caveats are not evidence. Never cite an unread artifact.
A short result preview is not proof of support for a claim.
An observed id records past access, not that its full contents remain visible.
Use read_artifact with the needed start_line/end_line range;
do not guess the missing contents.

Read relevant module reports and limitations if one is available.
Explanatory does not mean irrelevant: carry applicable residual risks into
your judgment. Do not read every historical report indiscriminately or substitute
reports for original measurements, code or literature.
When failed or blocked work remains, state a limitation describing its effect
on the scientific conclusion. Do not fabricate evidence or machine state.
"""


def _evidence_control_state(request: AgentRequest, state: AgentState) -> dict:
    observed = _observed_artifact_ids(state)
    authorized = [artifact.id for artifact in request.input_artifacts]
    pending = state.memory.get("pending_citation_artifact_ids", [])
    return {
        "observed_artifact_ids": observed,
        "unobserved_authorized_artifact_ids": sorted(set(authorized) - set(observed)),
        "pending_citation_artifact_ids": pending,
        "required_next_action": "read_artifact_or_remove_citation" if pending else "none",
    }


def build_context(
    request: AgentRequest, state: AgentState, *,
    datasets: DatasetAvailability | None = None,
    max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS,
) -> list[ContextSection | ContextMaterial]:
    reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
    sections = [
        ContextSection(
            name="evidence_control_state",
            content=json.dumps(_evidence_control_state(request, state)),
            priority=1000, required=True,
        ),
        ContextSection(
            name="research", content=json.dumps({"instruction": request.instruction}),
            priority=100, required=True,
        ),
        ContextSection(
            name="dataset_catalog",
            content=json.dumps(dataset_context(datasets or DatasetAvailability())),
            priority=98, required=True,
        ),
        ContextSection(
            name="input_artifacts",
            content=json.dumps([
                {"id": item.id, "kind": item.kind, "summary": item.summary}
                for item in request.input_artifacts
            ]),
            priority=95, required=True,
        ),
        *request_materials_context(request),
    ]
    for ref in request.input_artifacts:
        if ref.kind != "work_feedback" or ref.id not in request.resume_artifact_ids:
            continue
        feedback = read_artifact_json(reader, ref.id, WorkFeedback)
        sections.append(ContextSection(
            name="work_brief",
            content=json.dumps({
                "artifact_id": ref.id,
                **render_work_brief(
                    work_outcome=feedback.work_outcome,
                    previous_work_request=feedback.previous_work_request,
                    unresolved_task_outcomes=feedback.unresolved_task_outcomes,
                    authorized_artifacts=request.input_artifacts,
                ),
            }),
            priority=90, required=True,
        ))
    sections.extend(workspace_context(
        state, max_context_tokens=max_context_tokens, include_files=False,
    ))
    return sections
