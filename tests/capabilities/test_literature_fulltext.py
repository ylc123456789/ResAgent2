"""Fulltext tools use registered sources, preserve originals and reuse frozen results."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from resagent2_capabilities import (
    FetchLiteratureFulltextInput, FetchLiteratureFulltextTool,
    LiteratureSearchTool, LiteratureSearchToolInput,
)
from resagent2_components import ArtifactReadError, RegisteredArtifactReader
from resagent2_components.literature import LiteraturePaper
from resagent2_components.literature.fulltext import PdfFetchError, PdfParseError, PdfText
from resagent2_contracts import AgentOwner, SessionStatus
from resagent2_orchestrator import ArtifactRegistry
from resagent2_orchestrator.interpreter import build_research_index
from resagent2_runtime import AgentState


class Registration:
    def __init__(self, root):
        self.registry = ArtifactRegistry(root)
        self.refs = {}

    def register_scientific(self, candidate, *, run_id, session_id, content_bytes=None):
        ref = self.registry.register_scientific(
            candidate, run_id=run_id, session_id=session_id, content_bytes=content_bytes,
        )
        self.refs[ref.id] = ref
        return ref

    def list_artifacts(self, *, run_id):
        return [ref for ref in self.refs.values() if ref.run_id == run_id]

    def resolve(self, artifact_id, *, run_id):
        ref = self.refs.get(artifact_id)
        return ref if ref and ref.run_id == run_id else None


@pytest.fixture
def setup(tmp_path):
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_sci", agent_name="scientific", owner=AgentOwner.SCIENTIFIC,
        run_id="run_pdf", status=SessionStatus.ACTIVE, created_at=now, updated_at=now,
    )
    register = Registration(tmp_path)
    class Backend:
        def search(self, *args, **kwargs):
            return _search_result([LiteraturePaper(
                paper_id="arxiv:2401.12345v1", title="A paper", abstract="An abstract",
                source_url="https://arxiv.org/abs/2401.12345v1",
                pdf_url="https://arxiv.org/pdf/2401.12345v1",
            )])
    result = LiteratureSearchTool(Backend(), register).execute(
        state, LiteratureSearchToolInput(query="a paper"),
    )
    state.memory.update(result.memory_updates)
    paper_id = result.value["papers"][0]["artifact_id"]
    return state, register, paper_id


def download(url, path):
    path.write_bytes(b"%PDF-1.7\ncontrolled bytes")
    return path


def parse(path):
    assert path.read_bytes().startswith(b"%PDF-")
    return PdfText("# PDF text\n\n## Page 1\n\nExact text.", 1, [], "test-parser")


def test_fetch_freezes_original_text_and_index_source_chain(setup):
    state, register, paper_id = setup
    result = FetchLiteratureFulltextTool(register, download=download, parse=parse).execute(
        state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id),
    )
    assert result.ok
    pdf_id, text_id = result.value["pdf_artifact_id"], result.value["fulltext_artifact_id"]
    pdf, text = register.refs[pdf_id], register.refs[text_id]
    assert pdf.kind == "literature_pdf" and text.kind == "literature_fulltext"
    assert pdf.metadata["source_artifact_id"] == paper_id
    assert text.metadata["source_artifact_id"] == pdf_id
    assert text.metadata["paper_artifact_id"] == paper_id
    reader = RegisteredArtifactReader(list(register.refs.values()), run_id=state.run_id)
    reader.verify(pdf_id)
    assert "## Page 1" in reader.read_text(text_id)["content"]
    index = build_research_index(run_id=state.run_id, artifacts=list(register.refs.values()), work_requests=[])
    entries = {entry.artifact_id: entry for group in index.groups for entry in group.artifacts}
    assert entries[pdf_id].source_artifact_id == paper_id
    assert entries[text_id].source_artifact_id == pdf_id
    assert "read_artifact_ids" not in result.memory_updates


def test_repeated_fetch_uses_registered_bytes_after_tool_reconstruction(setup):
    state, register, paper_id = setup
    args = FetchLiteratureFulltextInput(paper_artifact_id=paper_id)
    first = FetchLiteratureFulltextTool(register, download=download, parse=parse).execute(state, args)
    def unexpected(*args):
        raise AssertionError("cached fulltext must not download or parse again")
    second = FetchLiteratureFulltextTool(register, download=unexpected, parse=unexpected).execute(state, args)
    assert second.value["cached"] is True
    assert second.value["fulltext_artifact_id"] == first.value["fulltext_artifact_id"]
    assert len(register.refs) == 4


def test_parse_failure_retains_original_and_retry_reuses_it(setup):
    state, register, paper_id = setup
    args = FetchLiteratureFulltextInput(paper_artifact_id=paper_id)
    def broken(path):
        raise PdfParseError("no text; OCR disabled")
    result = FetchLiteratureFulltextTool(register, download=download, parse=broken).execute(state, args)
    assert not result.ok and result.value["status"] == "parse_failed"
    pdf_id = result.value["pdf_artifact_id"]
    reader = RegisteredArtifactReader(list(register.refs.values()), run_id=state.run_id)
    assert reader.verified_path(pdf_id).read_bytes() == b"%PDF-1.7\ncontrolled bytes"
    assert pdf_id in result.memory_updates["literature_output_artifact_ids"]
    assert result.value["fulltext_artifact_id"] is None
    def unexpected(*args):
        raise AssertionError("retry must reuse the frozen original")
    retry = FetchLiteratureFulltextTool(register, download=unexpected, parse=parse).execute(state, args)
    assert retry.ok and retry.value["pdf_artifact_id"] == pdf_id


def test_download_failure_does_not_claim_original_or_fulltext(setup):
    state, register, paper_id = setup
    def unavailable(*args):
        raise PdfFetchError("HTTP 429")
    result = FetchLiteratureFulltextTool(register, download=unavailable).execute(
        state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id),
    )
    assert not result.ok and result.value["status"] == "download_failed"
    assert result.value["pdf_artifact_id"] is None
    assert result.value["fulltext_artifact_id"] is None
    assert len(register.refs) == 2


def test_unknown_or_wrong_kind_source_cannot_start_download(setup):
    state, register, paper_id = setup
    def unexpected(*args):
        raise AssertionError("must validate the registered paper before downloading")
    tool = FetchLiteratureFulltextTool(register, download=unexpected)
    receipt = next(ref.id for ref in register.refs.values() if ref.kind == "literature_search")
    for bad in ("artifact_unknown", receipt):
        with pytest.raises(ValueError, match="registered paper"):
            tool.execute(state, FetchLiteratureFulltextInput(paper_artifact_id=bad))
    other = state.model_copy(update={"run_id": "run_other"})
    with pytest.raises(ValueError, match="registered paper"):
        tool.execute(other, FetchLiteratureFulltextInput(paper_artifact_id=paper_id))


def test_changed_frozen_original_is_rejected_even_when_text_cached(setup):
    state, register, paper_id = setup
    args = FetchLiteratureFulltextInput(paper_artifact_id=paper_id)
    result = FetchLiteratureFulltextTool(register, download=download, parse=parse).execute(state, args)
    reader = RegisteredArtifactReader(list(register.refs.values()), run_id=state.run_id)
    reader.verified_path(result.value["pdf_artifact_id"]).write_bytes(b"changed")
    with pytest.raises(ArtifactReadError, match="sha256"):
        FetchLiteratureFulltextTool(register).execute(state, args)


def test_updated_pdf_location_is_a_new_snapshot_not_an_overwrite(tmp_path):
    from pathlib import Path
    from urllib.parse import urlparse
    register = Registration(tmp_path)
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_sci", agent_name="scientific", owner=AgentOwner.SCIENTIFIC,
        run_id="run_updates", status=SessionStatus.ACTIVE, created_at=now, updated_at=now,
    )
    class Backend:
        current = LiteraturePaper(paper_id="openalex:W1", title="Paper",
                                  abstract="Abstract", source_url="https://openalex.org/W1")
        def search(self, *args, **kwargs):
            return _search_result([self.current])
    backend = Backend()
    tool = LiteratureSearchTool(backend, register)
    args = LiteratureSearchToolInput(query="paper")
    first = tool.execute(state, args).value["papers"][0]["artifact_id"]
    old = register.refs[first]
    frozen = Path(urlparse(old.uri).path).read_bytes()
    backend.current = backend.current.model_copy(update={"pdf_url": "https://example.org/paper.pdf"})
    second = tool.execute(state, args).value["papers"][0]["artifact_id"]
    assert second != first
    assert register.refs[second].metadata["paper_key"] == old.metadata["paper_key"]
    assert Path(urlparse(old.uri).path).read_bytes() == frozen
    assert register.refs[first] == old
    assert tool.execute(state, args).value["papers"][0]["artifact_id"] == second
    fetched = FetchLiteratureFulltextTool(register, download=download, parse=parse).execute(
        state, FetchLiteratureFulltextInput(paper_artifact_id=second),
    )
    assert fetched.ok


def test_same_binary_under_different_sources_keeps_distinct_provenance(tmp_path):
    from resagent2_contracts import ArtifactCandidate
    registry = ArtifactRegistry(tmp_path)
    base = dict(kind="literature_pdf", path="paper.pdf", media_type="application/pdf", summary="PDF")
    first = registry.register_scientific(
        ArtifactCandidate(**base, metadata={"source_artifact_id": "artifact_paper1"}),
        run_id="run_same", session_id="session_same", content_bytes=b"%PDF-1.7",
    )
    second = registry.register_scientific(
        ArtifactCandidate(**base, metadata={"source_artifact_id": "artifact_paper2"}),
        run_id="run_same", session_id="session_same", content_bytes=b"%PDF-1.7",
    )
    assert first.id != second.id and first.sha256 == second.sha256


def test_persisted_literature_reuses_sources_after_registration_restart(setup, tmp_path):
    from resagent2_contracts import ArtifactCandidate, ResearchRequest, RunBudget, RunPermissions, RunStatus
    from resagent2_orchestrator import JsonRunStore, ResearchRun, ScientificArtifactRegistration

    state, initial, paper_id = setup
    store = JsonRunStore(tmp_path / "state")
    registry = initial.registry
    control = registry.register_system_artifact(
        ArtifactCandidate(kind="conclusion_requirements", path="requirements.json",
                          media_type="application/json", summary="Control", content="{}"),
        run_id=state.run_id, source_type="conclusion_requirement",
    )
    store.save(ResearchRun(
        run_id=state.run_id, request=ResearchRequest(
            goal="Read paper", budget=RunBudget(max_llm_calls=10, timeout_seconds=60),
            permissions=RunPermissions(execute_commands=False, prepare_environment=False),
        ),
        status=RunStatus.RUNNING, created_at=state.created_at, updated_at=state.updated_at,
        artifacts={**initial.refs, control.id: control},
    ))
    first_registration = ScientificArtifactRegistration(registry, store)
    args = FetchLiteratureFulltextInput(paper_artifact_id=paper_id)
    first = FetchLiteratureFulltextTool(
        first_registration, download=download, parse=parse,
    ).execute(state, args)

    # Recreate both bridge and store, as a new CLI process would.
    restarted = ScientificArtifactRegistration(
        ArtifactRegistry(tmp_path), JsonRunStore(tmp_path / "state"),
    )
    def unexpected(*args):
        raise AssertionError("restart must use already frozen source and text")
    second = FetchLiteratureFulltextTool(
        restarted, download=unexpected, parse=unexpected,
    ).execute(state, args)
    assert second.ok and second.value["cached"]
    assert second.value["fulltext_artifact_id"] == first.value["fulltext_artifact_id"]
    assert restarted.resolve(paper_id, run_id=state.run_id) == initial.refs[paper_id]
    assert restarted.resolve(paper_id, run_id="run_other") is None
    assert restarted.resolve(control.id, run_id=state.run_id) is None
    assert control.id not in {r.id for r in restarted.list_artifacts(run_id=state.run_id)}

    # The bridge may add only Scientific outputs, never hidden control records.
    reader = RegisteredArtifactReader(
        [], run_id=state.run_id,
        resolve=lambda id_: restarted.resolve(id_, run_id=state.run_id),
    )
    with pytest.raises(ArtifactReadError, match="unknown artifact id"):
        reader.read_text(control.id)


def test_changed_paper_snapshot_preserves_old_fulltext_cache(setup):
    state, register, first_paper = setup
    fetch = FetchLiteratureFulltextTool(register, download=download, parse=parse)
    first = fetch.execute(state, FetchLiteratureFulltextInput(paper_artifact_id=first_paper))
    old_refs = dict(register.refs)
    class UpdatedBackend:
        def search(self, *args, **kwargs):
            paper = LiteraturePaper.model_validate(old_refs[first_paper].metadata["paper"])
            return _search_result([paper.model_copy(update={"abstract": "Updated source abstract"})])
    searched = LiteratureSearchTool(UpdatedBackend(), register).execute(
        state, LiteratureSearchToolInput(query="a paper"),
    )
    second_paper = searched.value["papers"][0]["artifact_id"]
    second = fetch.execute(state, FetchLiteratureFulltextInput(paper_artifact_id=second_paper))
    assert second_paper != first_paper
    for key in ("pdf_artifact_id", "fulltext_artifact_id"):
        assert second.value[key] != first.value[key]
    assert all(register.refs[id_] == ref for id_, ref in old_refs.items())
    cached = fetch.execute(state, FetchLiteratureFulltextInput(paper_artifact_id=first_paper))
    assert cached.ok and cached.value["cached"]
    assert cached.value["fulltext_artifact_id"] == first.value["fulltext_artifact_id"]

def test_authorized_imported_pdf_is_parsed_offline_without_exposing_all_run_inputs(setup):
    state, register, paper_id = setup
    fetched = FetchLiteratureFulltextTool(register, download=download, parse=parse).execute(
        state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id),
    )
    paper = register.refs[paper_id]
    pdf = register.refs[fetched.value["pdf_artifact_id"]]
    # Imported refs are supplied by the controller; the live bridge still lists
    # Scientific outputs only, as it does in production.
    imported = [ref.model_copy(update={
        "producer": AgentOwner.ORCHESTRATOR, "session_id": None,
        "metadata": {**ref.metadata, "source_type": "import"},
    }) for ref in (paper, pdf)]
    register.refs.clear()
    def unexpected(*args):
        raise AssertionError("an imported local PDF must not trigger a download")
    tool = FetchLiteratureFulltextTool(
        register, input_artifacts=imported, download=unexpected, parse=parse,
    )
    result = tool.execute(state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id))
    assert result.ok and result.value["pdf_artifact_id"] == pdf.id
    assert result.value["fulltext_artifact_id"] in register.refs
    assert result.value["fulltext_artifact_id"] == fetched.value["fulltext_artifact_id"]
    assert len(register.refs) == 1
    # A new tool instance on the same authorized inputs reuses the generated text.
    cached = FetchLiteratureFulltextTool(
        register, input_artifacts=imported, download=unexpected, parse=unexpected,
    ).execute(state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id))
    assert cached.ok and cached.value["cached"]
    # Omitting the grant cannot recover the controller-owned paper by guessing its ID.
    with pytest.raises(ValueError, match="registered paper"):
        FetchLiteratureFulltextTool(register, download=unexpected).execute(
            state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id),
        )


def test_imported_inputs_from_other_run_or_mismatched_pdf_are_rejected(setup):
    state, register, paper_id = setup
    fetched = FetchLiteratureFulltextTool(register, download=download, parse=parse).execute(
        state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id),
    )
    paper = register.refs[paper_id]
    pdf = register.refs[fetched.value["pdf_artifact_id"]]
    register.refs.clear()
    def unexpected(*args):
        raise AssertionError("must reject invalid grants before accessing a PDF")
    foreign = paper.model_copy(update={"run_id": "run_other"})
    with pytest.raises(ValueError, match="registered paper"):
        FetchLiteratureFulltextTool(register, input_artifacts=[foreign], download=unexpected).execute(
            state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id),
        )
    bad_pdf = pdf.model_copy(update={"metadata": {
        **pdf.metadata, "source_artifact_id": "artifact_other_paper",
    }})
    with pytest.raises(ValueError, match="source paper"):
        FetchLiteratureFulltextTool(
            register, input_artifacts=[paper, bad_pdf], download=unexpected, parse=unexpected,
        ).execute(state, FetchLiteratureFulltextInput(paper_artifact_id=paper_id))


def _search_result(papers):
    from resagent2_components import LiteratureSearchResult
    return LiteratureSearchResult(papers=papers, source="arxiv", executed_query="test query",
                                  page=1, next_page=None, source_attempts=[{
                                      "source": "arxiv", "status": "success",
                                      "error_type": None, "error": None,
                                  }])
