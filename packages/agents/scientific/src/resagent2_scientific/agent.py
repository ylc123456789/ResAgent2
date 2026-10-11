"""Scientific reasoning through the common Agent invocation protocol."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner, AgentRequest, AgentResult, ArtifactCandidate, ConclusionRequirements,
    ErrorCode, ModuleError, ModuleStatus, ObservationTrace, ScientificAssessment,
    scientific_session_id,
)
from resagent2_components import (
    ArtifactRegistrationPort, LiteratureSearchBackend, RegisteredArtifactReader, WebPageFetcher, WebSearchBackend,
    ResourceLayout, request_dataset_refs, resolve_dataset_refs,
)
from resagent2_components.literature.fulltext import PdfText
from resagent2_capabilities import (FetchLiteratureTool, LiteratureSearchTool, ReadArtifactTool, WebFetchTool, WebSearchTool)
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentDefinition, AgentLoop, AgentState,
    AllowListPermissionPolicy, InMemorySessionStore, LLMClient, SessionStore,
)
from resagent2_runtime.tool_calling import contract_fingerprint, native_tool_schemas

from .completion import ScientificCompletionCheck, _observed_artifact_ids
from .context import SCIENTIFIC_PROMPT, build_context
from .tools import AskUserTool, FinishTool, RequestWorkTool


class ScientificAgent:
    """Produce scientific judgments and control requests from registered evidence."""

    def __init__(
        self, llm_client: LLMClient, *,
        literature_backend: LiteratureSearchBackend | None = None,
        literature_parser: Callable[[Path], PdfText] | None = None,
        web_search_backend: WebSearchBackend | None = None,
        web_page_fetcher: WebPageFetcher | None = None,
        registration_port: ArtifactRegistrationPort | None = None,
        store: SessionStore | None = None,
        max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS,
        resource_layout: ResourceLayout | None = None,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self.llm_client = llm_client
        self.literature_backend = literature_backend
        self.literature_parser = literature_parser
        self.web_search_backend = web_search_backend
        self.web_page_fetcher = web_page_fetcher
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
        session_id = request.parent_session_id or scientific_session_id(request.run_id)
        resolve = getattr(self.registration_port, "resolve", None)
        reader = RegisteredArtifactReader(
            request.input_artifacts, run_id=request.run_id,
            resolve=(lambda artifact_id: resolve(artifact_id, run_id=request.run_id, session_id=session_id)) if resolve else None,
        )
        list_registered = getattr(self.registration_port, "list_artifacts", None)
        try:
            datasets = resolve_dataset_refs(
                self.resource_layout.dataset_root, request_dataset_refs(request),
            )
            conclusion = ConclusionRequirements.model_validate(
                request.task_context.get("conclusion_requirements", {})
            )
            requirements = list(conclusion.required_evidence_kinds)
            required_artifacts = list(conclusion.required_artifacts)
            unresolved = []
            unresolved = [task for result in request.work_results for task in result.tasks
                          if task.status in {"failed", "blocked"}]
        except (OSError, ValueError, KeyError, TypeError) as error:
            return self._failure(str(error))
        key = self._idempotency_key(request)
        owned = self._owned_session(session_id, request.run_id)
        tools = [ReadArtifactTool(reader),
                 RequestWorkTool(allowed=request.permissions.request_work, reader=reader),
                 AskUserTool(reader), FinishTool()]
        if self.web_search_backend is not None:
            tools.append(WebSearchTool(self.web_search_backend))
        if self.web_page_fetcher is not None and self.registration_port is not None:
            tools.append(WebFetchTool(self.web_page_fetcher, self.registration_port,
                                      input_artifacts=request.input_artifacts))
        if self.literature_backend is not None:
            tools.append(LiteratureSearchTool(self.literature_backend))
        if self.registration_port is not None:
            tools.append(FetchLiteratureTool(
                self.registration_port, backend=self.literature_backend, input_artifacts=request.input_artifacts,
                parse=self.literature_parser,
            ))
        definition = AgentDefinition(
            name="scientific", owner=AgentOwner.SCIENTIFIC,
            system_prompt=SCIENTIFIC_PROMPT, tools=tuple(tools), llm_client=self.llm_client,
            context_builder=lambda request, state, limit: build_context(
                request, state, datasets=datasets, reader=reader,
            ),
            permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
            completion_check=ScientificCompletionCheck(
                unresolved, list(dict.fromkeys(requirements)),
                required_artifacts=required_artifacts,
                resolve_artifact=reader.resolve_ref, reader=reader,
                input_artifact_ids=[item.id for item in request.input_artifacts],
                registered_artifacts=(lambda: list_registered(run_id=request.run_id, session_id=session_id)) if list_registered else None,
                failed_work=any(result.status == "failed" for result in request.work_results),
            ),
            max_context_tokens=self.max_context_tokens,
            acknowledge_registration=getattr(self.registration_port, "acknowledge_registration", None),
            recover_registration=getattr(self.registration_port, "recover_registration", None),
        )
        if owned is not None:
            fingerprint = contract_fingerprint(definition.system_prompt, native_tool_schemas(definition.tools))
            if (owned.contract_fingerprint != fingerprint
                    or owned.tool_protocol_key != getattr(self.llm_client, "tool_session_key", None)):
                return self._failure("Stored Scientific session has a different fixed contract or native protocol")
            cached = owned.memory.get("_invocation_results", {}).get(key) if key is not None else None
            if cached is not None:
                try:
                    return AgentResult.model_validate(cached).model_copy(update={"llm_calls": 0})
                except ValidationError:
                    return self._failure("Stored Scientific result is invalid")
        if request.parent_session_id is None and self.store.exists(session_id):
            request = request.model_copy(update={"parent_session_id": session_id})
        result = self.loop.run(definition, request, session_id=session_id, initial_memory={})
        owned = self._owned_session(session_id, request.run_id)
        if owned is not None and result.session is not None and result.session.id == session_id:
            artifacts = list(result.artifacts)
            delivered = {item.id for item in artifacts if hasattr(item, "id")}
            delivered.update(item.id for item in request.input_artifacts)
            for artifact_id in owned.authorized_artifact_ids:
                ref = reader.resolve_ref(artifact_id)
                if ref is not None and ref.id not in delivered:
                    artifacts.append(ref)
                    delivered.add(ref.id)
            if result.status in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS, ModuleStatus.FAILED}:
                artifacts.append(ArtifactCandidate(
                    kind="observation_trace", path="observation_trace.json",
                    media_type="application/json", summary="Historical Scientific artifact-access log; not evidence qualification",
                    content=ObservationTrace(observed_artifact_ids=_observed_artifact_ids(owned)).model_dump_json(),
                ))
            result = result.model_copy(update={"artifacts": artifacts})
            if key is not None:
                cached = dict(owned.memory.get("_invocation_results", {}))
                cached[key] = result.model_dump(mode="json")
                owned.memory["_invocation_results"] = cached
            self.store.save(owned)
        return result

    @staticmethod
    def _idempotency_key(request: AgentRequest) -> str | None:
        if request.control_ack is not None:
            origin = request.control_ack
            return f"control:{origin.session_id}:{origin.turn}:{origin.call_id}"
        return "first" if request.parent_session_id is None else None

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
