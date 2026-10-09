"""Actual search/freeze/read/context and full-text chains; scripted, no network."""

from e2e.native_fixtures import tool_turns

import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from resagent2_components import ArtifactReadError, LiteraturePaper, LiteratureSearchResult, RegisteredArtifactReader
from resagent2_components.literature.fulltext import PdfText
from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, ResearchRequest, RunBudget, RunPermissions,
    WorkflowAgentRegistry,
)
from resagent2_orchestrator import (
    ArtifactRegistry, DeterministicWorkInterpreter, ResearchController,
    ScientificArtifactRegistration, WorkflowScheduler,
)
from resagent2_orchestrator.interpreter import build_research_index
from resagent2_scientific import ScientificAgent


TAIL_EVIDENCE = "TAIL_PAPER_RESULT: the measured improvement was 1.2 percentage points."
RUN_ID = "run_literature_windows"


def _papers():
    return [
        LiteraturePaper(
            paper_id=f"2401.0000{index}",
            title=f"Controlled comparison {index}",
            authors=["Example Researcher"],
            published_at=date(2024, 1, 1),
            abstract=("Controlled settings and matched baselines. " * 40)
            + (TAIL_EVIDENCE if index == 5 else "No tail result in this paper."),
            source_url=f"https://arxiv.org/abs/2401.0000{index}",
            pdf_url=f"https://arxiv.org/pdf/2401.0000{index}",
        )
        for index in range(6)
    ]


def _path(ref):
    return Path(url2pathname(urlparse(ref.uri).path))


def _finish(evidence, statement):
    return {"tool": "finish", "arguments": {
        "report": statement,
        "artifacts": [{
            "kind": "scientific_opinion", "path": "opinion.json",
            "media_type": "application/json", "summary": "Scientific conclusion",
            "content": json.dumps({
                "verdict": "supports", "statement": statement,
                "evidence_artifact_ids": [evidence],
            }),
        }],
    }}


def _controller(tmp_path, client, papers):
    class Backend:
        def search(self, query, **kwargs):
            limit, page = kwargs["max_results"], kwargs["page"]
            return LiteratureSearchResult(
                papers=papers[(page - 1) * limit:page * limit], source="arxiv",
                executed_query=query, page=page,
                next_page=page + 1 if page * limit < len(papers) else None,
                total_results=len(papers),
            )

    scheduler = WorkflowScheduler(
        bindings={}, artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    registration = ScientificArtifactRegistration(scheduler.artifact_registry, scheduler.store)
    client.registration = registration
    agent = ScientificAgent(client, literature_backend=Backend(), registration_port=registration)
    controller = ResearchController(
        scientific_port=agent, compiler=None, scheduler=scheduler,
        registry=WorkflowAgentRegistry(definitions=[]), interpreter=DeterministicWorkInterpreter(),
    )
    return controller, agent


def _registered(client, kind):
    return [ref for ref in client.registration.list_artifacts(run_id=RUN_ID) if ref.kind == kind]


def test_search_continuation_reaches_scientific_with_complete_abstract(tmp_path):
    class Client:
        tool_session_key = "test-native-tools/v1"

        step = 0

        def next_tool_call(self, context, schemas, turns, *, max_input_tokens):
            self.step += 1
            if self.step == 1:
                return tool_turns([{"tool": "literature_search", "arguments": {"query": "comparison"}}])[0]
            receipts = _registered(self, "literature_search")
            receipt = json.loads(_path(receipts[-1]).read_text())
            if self.step == 2:
                assert len(_registered(self, "literature_paper")) == 5
                assert receipt["requested_source"] == "auto"
                assert receipt["source"] == "arxiv"
                assert receipt["next_request"]["max_results"] == 5
                assert receipt["next_request"]["page"] == 2
                assert receipt["next_request"]["source"] == "arxiv"
                return tool_turns([{"tool": "literature_search", "arguments": receipt["next_request"]}])[0]
            assert len(_registered(self, "literature_paper")) == 6
            ref = next(ref for ref in _registered(self, "literature_paper")
                       if ref.metadata["paper"]["paper_id"] == "2401.00005")
            if self.step == 3:
                assert receipt["page"] == 2
                assert receipt["next_request"] is None
                assert receipt["total_results"] == 6
                assert TAIL_EVIDENCE not in context.text
                return tool_turns([{"tool": "read_artifact", "arguments": {"artifact_id": ref.id}}])[0]
            assert self.step == 4
            assert TAIL_EVIDENCE in context.text
            return tool_turns([_finish(ref.id, TAIL_EVIDENCE)])[0]

    controller, _ = _controller(tmp_path, Client(), _papers())
    run = controller.create_run(RUN_ID, ResearchRequest(
        goal="Locate and read the last comparison's evidence",
        required_evidence_kinds=["literature_paper"],
        budget=RunBudget(max_llm_calls=6, timeout_seconds=30),
        permissions=RunPermissions(execute_commands=False, prepare_environment=False),
    ))
    assert run.status == "completed", run.terminal_error
    assert run.completion_violations == []
    assert len([ref for ref in run.artifacts.values() if ref.kind == "literature_paper"]) == 6


def test_literature_tail_reaches_actual_scientific_context(tmp_path):
    class Client:
        tool_session_key = "test-native-tools/v1"

        step = 0
        tail_line = None

        def next_tool_call(self, context, schemas, turns, *, max_input_tokens):
            self.step += 1
            if self.step == 1:
                return tool_turns([{"tool": "literature_search", "arguments": {"query": "comparison", "max_results": 6}}])[0]
            papers = _registered(self, "literature_paper")
            assert len(papers) == 6
            ref = next(ref for ref in papers if ref.metadata["paper"]["paper_id"] == "2401.00005")
            if self.step == 2:
                body = _path(ref).read_text()
                assert body.index(TAIL_EVIDENCE) > 200
                assert TAIL_EVIDENCE not in context.text  # Not in search previews.
                receipt = _registered(self, "literature_search")[0]
                receipt_body = json.loads(_path(receipt).read_text())
                assert set(receipt_body["paper_artifact_ids"]) == {paper.id for paper in papers}
                assert TAIL_EVIDENCE not in _path(receipt).read_text()
                lines = body.splitlines()
                self.tail_line = next(i for i, line in enumerate(lines, 1) if TAIL_EVIDENCE in line)
                assert self.tail_line > 1
                return tool_turns([{"tool": "read_artifact", "arguments": {
                    "artifact_id": ref.id,
                    "start_line": self.tail_line, "end_line": self.tail_line,
                }}])[0]
            assert self.step == 3
            assert "artifact_reads" in context.included_sections
            section = context.text.split("## artifact_reads\n", 1)[1].split("\n\n## ", 1)[0]
            snippets = json.loads(section.split("\n", 1)[1])["snippets"]
            assert len(snippets) == 1
            assert snippets[0]["artifact_id"] == ref.id
            assert snippets[0]["truncated"] is False
            assert TAIL_EVIDENCE in snippets[0]["content"]
            return tool_turns([_finish(ref.id, TAIL_EVIDENCE)])[0]

    client = Client()
    controller, agent = _controller(tmp_path, client, _papers())
    run = controller.create_run(RUN_ID, ResearchRequest(
        goal="Read the final comparison's saved abstract", required_evidence_kinds=["literature_paper"],
        budget=RunBudget(max_llm_calls=5, timeout_seconds=30),
        permissions=RunPermissions(execute_commands=False, prepare_environment=False),
    ))
    assert run.status == "completed", run.terminal_error
    assert run.completion_violations == []
    state = agent.store.load(run.scientific_session.id)
    read = next(e for e in state.events if e.tool == "read_artifact" and e.type == "observation")
    assert read.data["value"]["truncated"] is False
    assert TAIL_EVIDENCE in read.data["value"]["content"]
    assert "read_artifact_summaries" not in state.memory

    ref = run.artifacts[run.final_opinion.evidence_artifact_ids[0]]
    path = _path(ref)
    assert ref.kind == "literature_paper" and ref.media_type == "text/markdown"
    assert path.name.startswith("paper_")
    assert "# Controlled comparison 5" in path.read_text()
    assert "Controlled comparison 4" not in path.read_text()
    assert "not paper full text" in path.read_text()
    frozen = path.read_bytes()
    assert hashlib.sha256(frozen).hexdigest() == ref.sha256

    # Changing another line must still fail whole-file integrity for a narrow read.
    path.write_bytes(b" " + frozen[1:])
    with pytest.raises(ArtifactReadError, match="sha256"):
        RegisteredArtifactReader([ref], run_id=RUN_ID).read_text(
            ref.id, start_line=client.tail_line, end_line=client.tail_line,
        )


def test_search_fetch_read_finish_preserves_registered_source_chain(tmp_path, monkeypatch):
    from resagent2_capabilities.literature import fulltext as fulltext_tool

    evidence = "FULLTEXT_ONLY: held-out accuracy was 0.83 under a fixed evaluation protocol."
    calls = []

    def download(url, destination):
        calls.append(("download", url))
        destination.write_bytes(b"%PDF-1.7\ncontrolled source bytes\n%%EOF")
        return destination

    def parse(path):
        calls.append(("parse", Path(path)))
        assert Path(path).read_bytes().startswith(b"%PDF-1.7")
        return PdfText(
            markdown=f"## Page 1\n\n{evidence}\n", page_count=1,
            warnings=[], parser_version="controlled-test-parser",
        )

    monkeypatch.setattr(fulltext_tool, "fetch_pdf", download)
    monkeypatch.setattr(fulltext_tool, "parse_pdf", parse)

    class Client:
        tool_session_key = "test-native-tools/v1"

        step = 0

        def next_tool_call(self, context, schemas, turns, *, max_input_tokens):
            self.step += 1
            if self.step == 1:
                return tool_turns([{"tool": "literature_search", "arguments": {"query": "controlled result"}}])[0]
            paper = _registered(self, "literature_paper")[0]
            if self.step == 2:
                assert evidence not in context.text
                return tool_turns([{"tool": "fetch_literature_fulltext", "arguments": {"paper_artifact_id": paper.id}}])[0]
            text = _registered(self, "literature_fulltext")[0]
            if self.step == 3:
                assert evidence not in context.text  # Obtaining a file did not return its text.
                return tool_turns([{"tool": "read_artifact", "arguments": {"artifact_id": text.id}}])[0]
            assert self.step == 4
            assert evidence in context.text
            assert "artifact_reads" in context.included_sections
            return tool_turns([_finish(text.id, evidence)])[0]

    client = Client()
    controller, agent = _controller(tmp_path, client, _papers()[:1])
    run = controller.create_run(RUN_ID, ResearchRequest(
        goal="Check the evaluation protocol from the retrieved paper's full text",
        required_evidence_kinds=["literature_fulltext"],
        budget=RunBudget(max_llm_calls=6, timeout_seconds=30),
        permissions=RunPermissions(execute_commands=False, prepare_environment=False),
    ))
    assert run.status == "completed", run.terminal_error
    assert run.completion_violations == []
    assert [name for name, _ in calls] == ["download", "parse"]
    by_kind = {ref.kind: ref for ref in run.artifacts.values()}
    paper, pdf, text = [by_kind[kind] for kind in ("literature_paper", "literature_pdf", "literature_fulltext")]
    assert run.final_opinion.evidence_artifact_ids == [text.id]
    assert pdf.metadata["paper_artifact_id"] == paper.id
    assert text.metadata["paper_artifact_id"] == paper.id
    assert text.metadata["ocr"] is False
    index = build_research_index(
        run_id=RUN_ID, artifacts=list(run.artifacts.values()), work_requests=run.work_requests,
    )
    entries = {item.artifact_id: item for group in index.groups for item in group.artifacts}
    assert entries[paper.id].source_artifact_id is None
    assert entries[pdf.id].source_artifact_id == paper.id
    assert entries[text.id].source_artifact_id == pdf.id
    state = agent.store.load(run.scientific_session.id)
    observations = [event for event in state.events if event.type == "observation"]
    assert [event.tool for event in observations] == [
        "literature_search", "fetch_literature_fulltext", "read_artifact", "finish",
    ]
    read = next(event for event in observations if event.tool == "read_artifact")
    assert evidence in read.data["value"]["content"]


def test_registration_does_not_rewrite_existing_frozen_paper_bytes(tmp_path):
    registry = ArtifactRegistry(tmp_path / "artifacts")
    candidate = ArtifactCandidate(
        kind="literature_paper", path="paper.json", media_type="application/json",
        summary="Controlled comparison", metadata={"paper": _papers()[0].model_dump(mode="json")},
    )
    old_bytes = json.dumps(candidate.metadata, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(old_bytes).hexdigest()
    old_id = f"artifact_sci_{digest[:16]}"
    old_path = registry.root / RUN_ID / old_id / candidate.path
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(old_bytes)
    old_ref = ArtifactRef(
        id=old_id, kind=candidate.kind, producer=AgentOwner.SCIENTIFIC,
        run_id=RUN_ID, session_id="session_literature_windows",
        uri=old_path.as_uri(), sha256=digest, media_type=candidate.media_type,
        summary=candidate.summary, metadata=candidate.metadata,
    )
    new_ref = registry.register_scientific(candidate, run_id=RUN_ID, session_id=old_ref.session_id)
    assert new_ref.id != old_ref.id and new_ref.uri != old_ref.uri
    assert old_path.read_bytes() == old_bytes
    assert json.loads(_path(new_ref).read_bytes()) == json.loads(old_bytes)
    assert len(_path(new_ref).read_text().splitlines()) > 1
    old_read = RegisteredArtifactReader([old_ref], run_id=RUN_ID).read_text(old_ref.id)
    assert old_read["content"] == old_bytes.decode("utf-8")
    assert old_read["truncated"] is False
