"""Scientific reasoning through the common Agent invocation protocol."""

from __future__ import annotations

from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner, AgentRequest, AgentResult, ArtifactCandidate, ConclusionRequirements,
    ErrorCode, ModuleError, ModuleStatus, ObservationTrace, ScientificAssessment, WorkFeedback, WorkRecord,
    scientific_session_id,
)
from resagent2_components import (
    ArtifactRegistrationPort, LiteratureSearchBackend, RegisteredArtifactReader,
    ResourceLayout, read_artifact_json, read_request_material, request_dataset_refs, resolve_dataset_refs,
)
from resagent2_capabilities import LiteratureSearchTool, ReadArtifactTool
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentDefinition, AgentLoop, AgentState,
    AllowListPermissionPolicy, InMemorySessionStore, LLMClient, SessionStore,
)

from .completion import ScientificCompletionCheck, _observed_artifact_ids
from .context import SCIENTIFIC_PROMPT, build_context
from .models import ScientificAction
from .tools import AskUserTool, FinishTool, RequestWorkTool


class ScientificAgent:
    """Produce scientific judgments and control requests from registered evidence."""

    def __init__(
        self, llm_client: LLMClient, *,
        literature_backend: LiteratureSearchBackend | None = None,
        registration_port: ArtifactRegistrationPort | None = None,
        store: SessionStore | None = None,
        max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS,
        resource_layout: ResourceLayout | None = None,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self.llm_client = llm_client
        self.literature_backend = literature_backend
        self.registration_port = registration_port
        self.store = store or InMemorySessionStore()
        self.max_context_tokens = max_context_tokens
        self.resource_layout = resource_layout or ResourceLayout.from_env()
        self.loop = AgentLoop(store=self.store)

    @staticmethod
    def _failure(message: str) -> AgentResult:
        return AgentResult(
            status=ModuleStatus.FAILED, report=message,
            error=ModuleError(code=ErrorCode.INVALID_INPUT, message=message, retryable=False),
        )

    def invoke(self, request: AgentRequest) -> AgentResult:
        try:
            request = AgentRequest.model_validate(request)
        except ValueError as error:
            return self._failure(str(error))
        if request.agent != AgentOwner.SCIENTIFIC:
            return self._failure("ScientificAgent received a non-Scientific request")
        resolve = getattr(self.registration_port, "resolve", None)
        reader = RegisteredArtifactReader(
            request.input_artifacts, run_id=request.run_id,
            resolve=(lambda artifact_id: resolve(artifact_id, run_id=request.run_id)) if resolve else None,
        )
        session_id = request.parent_session_id or scientific_session_id(request.run_id)
        try:
            datasets = resolve_dataset_refs(
                self.resource_layout.dataset_root, request_dataset_refs(request),
            )
            requirements = []
            required_artifacts = []
            unresolved = []
            for ref in request.input_artifacts:
                if ref.kind == "conclusion_requirements":
                    material = read_artifact_json(reader, ref.id, ConclusionRequirements)
                    requirements.extend(material.required_evidence_kinds)
                    required_artifacts.extend(material.required_artifacts)
            feedback_refs = [ref for ref in request.input_artifacts if ref.kind == "work_feedback"]
            if feedback_refs:
                current = next((ref for ref in feedback_refs if ref.id in request.resume_artifact_ids),
                               feedback_refs[-1])
                feedback = WorkFeedback.model_validate(read_request_material(request, current, reader=reader))
                record_ref = reader.resolve_ref(feedback.work_record_artifact_id)
                if record_ref is None or record_ref.kind != "work_record" or record_ref.session_id != session_id:
                    raise ValueError("Work feedback has no authorized work record for this session")
                record = read_artifact_json(reader, record_ref.id, WorkRecord)
                if (record.run_id != request.run_id or record.session_id != session_id
                        or record.work_request_id != feedback.work_request_id):
                    raise ValueError("Work record does not belong to this feedback")
                unresolved = record.unresolved_task_outcomes
        except (OSError, ValueError, KeyError, TypeError) as error:
            return self._failure(str(error))
        key = self._idempotency_key(request)
        owned = self._owned_session(session_id, request.run_id)
        if owned is not None:
            cached = owned.memory.get("_invocation_results", {}).get(key)
            if cached is not None:
                try:
                    return AgentResult.model_validate(cached).model_copy(update={"llm_calls": 0})
                except ValidationError:
                    return self._failure("Stored Scientific result is invalid")
        tools = [ReadArtifactTool(reader),
                 RequestWorkTool(allowed=request.permissions.request_work),
                 AskUserTool(), FinishTool()]
        if self.literature_backend is not None and self.registration_port is not None:
            tools.append(LiteratureSearchTool(self.literature_backend, self.registration_port))
        definition = AgentDefinition(
            name="scientific", owner=AgentOwner.SCIENTIFIC,
            system_prompt=SCIENTIFIC_PROMPT, tools=tuple(tools), llm_client=self.llm_client,
            context_builder=lambda request, state, limit: build_context(
                request, state, datasets=datasets, max_context_tokens=limit,
            ),
            permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
            completion_check=ScientificCompletionCheck(
                unresolved, list(dict.fromkeys(requirements)),
                required_artifacts=required_artifacts,
                resolve_artifact=reader.resolve_ref, reader=reader,
                input_artifact_ids=[item.id for item in request.input_artifacts],
            ),
            action_type=ScientificAction, max_context_tokens=self.max_context_tokens,
        )
        if request.parent_session_id is None and self.store.exists(session_id):
            request = request.model_copy(update={"parent_session_id": session_id})
        result = self.loop.run(definition, request, session_id=session_id, initial_memory={})
        owned = self._owned_session(session_id, request.run_id)
        if owned is not None and result.session is not None and result.session.id == session_id:
            artifacts = list(result.artifacts)
            if result.status == ModuleStatus.NEEDS_USER_INPUT and owned.memory.get("latest_assessment"):
                assessment = ScientificAssessment.model_validate(owned.memory["latest_assessment"])
                artifacts.append(ArtifactCandidate(
                    kind="scientific_assessment", path="scientific_assessment.json",
                    media_type="application/json", summary=assessment.statement,
                    content=assessment.model_dump_json(),
                ))
            delivered = {item.id for item in artifacts if hasattr(item, "id")}
            delivered.update(item.id for item in request.input_artifacts)
            for artifact_id in owned.memory.get("literature_artifact_ids", []):
                ref = reader.resolve_ref(artifact_id)
                if ref is not None and ref.id not in delivered:
                    artifacts.append(ref)
            artifacts.append(ArtifactCandidate(
                kind="observation_trace", path="observation_trace.json",
                media_type="application/json", summary="Evidence observed by Scientific tools",
                content=ObservationTrace(observed_artifact_ids=_observed_artifact_ids(owned)).model_dump_json(),
            ))
            result = result.model_copy(update={"artifacts": artifacts})
            cached = dict(owned.memory.get("_invocation_results", {}))
            cached[key] = result.model_dump(mode="json")
            owned.memory["_invocation_results"] = cached
            self.store.save(owned)
        return result

    @staticmethod
    def _idempotency_key(request: AgentRequest) -> str:
        if request.resume_artifact_ids:
            return "materials:" + ",".join(sorted(request.resume_artifact_ids))
        return "first" if request.parent_session_id is None else "resume"

    def _owned_session(self, session_id: str, run_id: str) -> AgentState | None:
        try:
            state = self.store.load(session_id)
        except Exception:
            return None
        if (
            state.session_id != session_id or state.run_id != run_id
            or state.owner != AgentOwner.SCIENTIFIC or state.agent_name != "scientific"
            or state.task_id is not None or state.attempt_number is not None
        ):
            return None
        return state
