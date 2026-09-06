"""Actual search/freeze/read/context chain, with scripted decisions, no network."""

import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from resagent2_capabilities import (
    ArtifactReadError,
    LiteraturePaper,
    RegisteredArtifactReader,
)
from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    ArtifactRef,
    ResearchRequest,
    RunBudget,
    ScientificTurnRequest,
    TaskBudget,
)
from resagent2_orchestrator import ArtifactRegistry
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
        )
        for index in range(6)
    ]


def _path(ref):
    return Path(url2pathname(urlparse(ref.uri).path))


def _candidate(papers):
    return ArtifactCandidate(
        kind="literature_search", path="literature_search.json",
        media_type="application/json", summary="Controlled comparisons",
        metadata={"papers": [paper.model_dump(mode="json") for paper in papers]},
    )


def test_literature_tail_reaches_actual_scientific_context(tmp_path):
    class Backend:
        def search(self, query, **kwargs):
            return _papers()

    class Registration:
        registry = ArtifactRegistry(tmp_path / "artifacts")
        ref = None
        candidate = None

        def register_scientific(self, candidate, *, run_id, session_id):
            self.candidate = candidate
            self.ref = self.registry.register_scientific(
                candidate, run_id=run_id, session_id=session_id,
            )
            return self.ref

        def resolve(self, artifact_id, *, run_id):
            ref = self.ref
            return ref if ref and (ref.id, ref.run_id) == (artifact_id, run_id) else None

    registration = Registration()

    class Client:
        step = 0
        tail_line = None

        def next_action(self, context, action_type):
            self.step += 1
            if self.step == 1:
                return {"tool": "literature_search", "arguments": {"query": "comparison"}}
            ref = registration.ref
            if self.step == 2:
                body = _path(ref).read_text()
                assert len(body.encode("utf-8")) > 8000
                assert body.index(TAIL_EVIDENCE) > 8000
                assert TAIL_EVIDENCE not in context.text  # Absent from search previews.
                lines = body.splitlines()
                self.tail_line = next(i for i, line in enumerate(lines, 1) if TAIL_EVIDENCE in line)
                assert self.tail_line > 1
                assert len(lines[self.tail_line - 1]) < 6000
                return {"tool": "read_artifact", "arguments": {
                    "artifact_id": ref.id,
                    "start_line": self.tail_line, "end_line": self.tail_line,
                }}
            assert "workspace_reads" in context.included_sections
            section = context.text.split("## workspace_reads\n", 1)[1].split("\n\n## ", 1)[0]
            snippets = json.loads(section.split("\n", 1)[1])["artifact_snippets"]
            assert len(snippets) == 1
            assert snippets[0]["artifact_id"] == ref.id
            assert snippets[0]["truncated"] is False
            assert TAIL_EVIDENCE in snippets[0]["content"]
            return {"tool": "finish", "arguments": {
                "summary": "Read the final paper's result from frozen evidence.",
                "opinion": {"verdict": "supports", "statement": TAIL_EVIDENCE,
                            "evidence_artifact_ids": [ref.id]},
            }}

    client = Client()
    agent = ScientificAgent(client, literature_backend=Backend(), registration_port=registration)
    result = agent.run(ScientificTurnRequest(
        run_id=RUN_ID,
        research=ResearchRequest(
            goal="Read the final comparison's result", required_evidence_kinds=["literature_search"],
            budget=RunBudget(max_tasks=1, max_attempts_per_task=1, max_llm_calls=5, timeout_seconds=30),
        ),
        budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=30),
    ))
    assert result.status == "completed"
    state = agent.store.load(result.session.id)
    read = next(e for e in state.events if e.tool == "read_artifact" and e.type == "observation")
    assert read.data["value"]["truncated"] is False
    assert TAIL_EVIDENCE in read.data["value"]["content"]
    assert "read_artifact_summaries" not in state.memory

    ref = registration.ref
    path = _path(ref)
    frozen = path.read_bytes()
    assert hashlib.sha256(frozen).hexdigest() == ref.sha256
    repeated = registration.registry.register_scientific(
        registration.candidate, run_id=RUN_ID, session_id=result.session.id,
    )
    assert repeated.id == ref.id and repeated.uri == ref.uri
    assert path.read_bytes() == frozen

    # Even a change outside the requested tail range must fail whole-file integrity.
    path.write_bytes(b" " + frozen[1:])
    with pytest.raises(ArtifactReadError, match="sha256"):
        RegisteredArtifactReader([ref], run_id=RUN_ID).read_text(
            ref.id, start_line=client.tail_line, end_line=client.tail_line,
        )


def test_multiline_registration_does_not_rewrite_a_legacy_frozen_file(tmp_path):
    registry = ArtifactRegistry(tmp_path / "artifacts")
    candidate = _candidate(_papers())
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
    new_ref = registry.register_scientific(
        candidate, run_id=RUN_ID, session_id=old_ref.session_id,
    )
    assert new_ref.id != old_ref.id and new_ref.uri != old_ref.uri
    assert old_path.read_bytes() == old_bytes
    assert json.loads(_path(new_ref).read_bytes()) == json.loads(old_bytes)
    assert len(_path(new_ref).read_text().splitlines()) > 1
    # Existing refs remain integrity-valid, without silently rewriting their bytes.
    old_read = RegisteredArtifactReader([old_ref], run_id=RUN_ID).read_text(old_ref.id)
    assert old_read["content"] == old_bytes.decode("utf-8")[:8000]
    assert old_read["truncated"] is True
