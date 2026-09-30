"""Scientific's single prompt and artifact-derived context."""

from __future__ import annotations

import json

from resagent2_components import (
    DatasetAvailability, RegisteredArtifactReader, dataset_context,
    read_artifact_json, request_materials_context, workspace_context,
)
from resagent2_components.artifacts import research_artifacts
from resagent2_contracts import (
    AgentRequest, ResearchArtifactEntry, ResearchIndex, ResearchIndexGroup,
)
from resagent2_runtime import DEFAULT_AGENT_CONTEXT_TOKENS, AgentState, ContextMaterial, ContextSection

from .completion import SCIENTIFIC_FINISH_ARTIFACT_KINDS, _observed_artifact_ids


SCIENTIFIC_PROMPT = f"""### Role and scope
You are the Scientific Agent responsible for the scientific direction and final
judgment of one research run. Interpret the instruction, investigate the literature,
assess evidence, and decide whether further work or a user decision is needed.
Use request_work for code inspection, implementation, or experiments; execution
Agents own that work. Literature search and scientific judgment remain yours.

### Inputs and evidence
The research index groups registered materials by their original work objective,
including prior rounds. Current work feedback presents the latest recorded task
reports and execution facts under that same objective. Keep the supplied
work/task/artifact identities and names when discussing or requesting follow-up work.
Use reports to understand progress, findings, and limitations. They are explanations,
not independent measurements; several reports derived from the same data do not
provide independent confirmation. Distinguish a report's interpretation from the
observations that support it, and carry relevant limitations into your judgment.
Read original artifacts for missing details, conflicting claims, or checks of the
key evidence behind a conclusion. Read historical reports only when needed.
Feedback can omit report text within the context budget; its registered source
contains the remainder. Work records, not report prose, establish execution state.

Reports and index entries do not observe the artifacts they mention. Cite only
ArtifactIds observed through read_artifact or literature_search; the system records
observation evidence independently. A short result preview is not proof of support.
An observed id records past access, not that its full contents remain visible.
Use read_artifact with the needed start_line/end_line range; do not guess the missing
contents. Assess whether the observed content actually supports the cited claim.

### Decision principles
Match the strength of each conclusion to the evidence and its limitations. Distinguish
an unsuccessful scientific hypothesis from an unsuccessful execution, and a failed
auxiliary check from the validity of the underlying result. Resolve material
contradictions or state their effect; do not turn missing evidence into a positive claim.
Use ask_user when a decision is reserved for the user or a default may not be inferred.
Do not replace required user decisions with request_work.

Include already-known prerequisites: if source is missing or broken, request that
change before the experiment that needs it. Do not run a known-broken experiment
merely to rediscover its stated problem. Distinguish known prerequisites from
hypothetical failures: when no problem is known, request execution first and request
repair only after a failure is observed. Preserve unmet constraints. Diagnose blocked
work from its work records and evidence; retry after identifying a relevant change.

### Working practices
request_work is not a substitute for your own tools. A timeout or HTTP 429 is not a
reason to delegate literature work. When the tool's own retries are exhausted, use
ask_user: explain the actual error and request supplied evidence, service recovery,
or an explicit decision on proceeding with limited evidence. Honor the user's
answer, retain the resulting limitations, and do not repeat unchanged failures.
Use only datasets in dataset_catalog. Ask for missing datasets; never invent a
path, download a dataset, or silently substitute one.

The supplied conclusion_requirements artifact specifies required evidence kinds
and optional required_artifacts. Read and cite registered evidence of the required
kinds; authorized imported evidence counts. required_artifacts names exact,
case-sensitive output_name values that must be registered in this Run. These logical
delivery names are not paths, artifact kinds, or citation requirements. Preserve each
explicit name in request_work's objective or constraints and request submission with
that output_name; do not infer names from the goal. Request missing work or ask the
user how to proceed. A filename alone does not satisfy delivery, and existence does
not establish scientific correctness. Your own named finish outputs are checked
after registration by the final gate.

### Completion and handoff
Finish with status="completed", a report, and exactly one scientific_opinion JSON
artifact. Insufficient evidence can warrant an inconclusive verdict, request_work,
or ask_user; status="failed" cannot bypass evidence and delivery requirements.
The opinion contains verdict, statement, evidence_artifact_ids, limitations,
unresolved_questions, and recommended_next_steps. Submit kind="scientific_opinion",
path="scientific_opinion.json", media_type="application/json", summary, and JSON
content. New artifact kinds allowed: {", ".join(sorted(SCIENTIFIC_FINISH_ARTIFACT_KINDS))}.
Cite existing evidence under its original ID instead of returning or copying it as
new output. Tool and system records are returned automatically.
The report explains the conclusion, its evidence, conditions and limitations, and
any remaining work; machines consume the opinion artifact. If failed or blocked
work remains, state how it limits the conclusion. Do not fabricate evidence or state.
"""


def _evidence_control_state(request: AgentRequest, state: AgentState) -> dict:
    observed = _observed_artifact_ids(state)
    pending = state.memory.get("pending_citation_artifact_ids", [])
    return {
        "observed_artifact_ids": observed,
        "pending_citation_artifact_ids": pending,
        "required_next_action": "read_artifact_or_remove_citation" if pending else "none",
    }


def _research_materials(request: AgentRequest, reader: RegisteredArtifactReader) -> dict:
    materials = {ref.id: ref for ref in research_artifacts(request.input_artifacts)}
    indexes = [ref for ref in request.input_artifacts if ref.kind == "research_index"]
    # The caller places the current snapshot last; prior snapshots stay authorized
    # so historical feedback links remain readable without entering the prompt.
    value = {"index_artifact_id": indexes[-1].id if indexes else None}
    if indexes:
        index = read_artifact_json(reader, indexes[-1].id, ResearchIndex)
        if index.run_id != request.run_id:
            raise ValueError("research index belongs to another Run")
        entries = {entry.artifact_id: entry for group in index.groups for entry in group.artifacts}
        if entries.keys() != materials.keys():
            raise ValueError("research index does not match the available materials")
        for artifact_id, entry in entries.items():
            ref = reader.resolve_ref(artifact_id)
            if ref is None or entry != ResearchArtifactEntry.from_ref(ref, execution_status=entry.execution_status):
                raise ValueError("research index entry does not match its registered artifact")
    else:
        # Standalone invocations use the same directory shape for their supplied materials.
        entries = [ResearchArtifactEntry.from_ref(ref) for ref in materials.values()]
        index = ResearchIndex(run_id=request.run_id, groups=[ResearchIndexGroup(
            key="inputs", title="Supplied research materials", artifacts=entries,
        )] if entries else [])
    value["index"] = index.model_dump(mode="json")
    return value


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
            name="research_materials",
            content=json.dumps(_research_materials(request, reader)),
            priority=95, required=True,
        ),
        *request_materials_context(request),
    ]
    sections.extend(workspace_context(
        state, max_context_tokens=max_context_tokens, include_files=False,
    ))
    return sections
