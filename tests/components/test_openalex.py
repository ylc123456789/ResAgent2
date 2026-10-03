import httpx
import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from resagent2_components import (
    MultiSourceLiteratureBackend,
    LiteraturePaper,
    LiteratureSearchError,
    LiteratureUnavailableError,
    OpenAlexLiteratureBackend,
)
from resagent2_capabilities import (
    LiteratureSearchTool,
    LiteratureSearchToolInput,
)
from resagent2_components.literature import (
    _http as _literature_http,
    backends as openalex,
)
from tests.capabilities.test_literature import _FakeBackend, _FakeRegister, state


@pytest.fixture(autouse=True)
def isolated_http(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(_literature_http.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(_literature_http.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(openalex, "_OPENALEX_HTTP", _literature_http.LiteratureHTTP("OpenAlex", interval_seconds=1))


def work():
    return {
        "id": "https://openalex.org/W123",
        "display_name": "A real-shaped record",
        "publication_date": "2020-01-02",
        "authorships": [{"author": {"display_name": "Alice"}}],
        "abstract_inverted_index": {"evidence.": [2], "Retrieved": [0], "abstract": [1]},
    }


def fake_response(monkeypatch, body):
    requests = []
    def open_request(request, *, timeout):
        requests.append((request, timeout))
        return httpx.Response(200, content=body, request=request)
    monkeypatch.setattr(openalex, "send_request", open_request)
    return requests


def test_query_auth_and_normalized_record(monkeypatch):
    requests = fake_response(monkeypatch, json.dumps({"results": [work(), work()]}).encode())
    backend = OpenAlexLiteratureBackend(api_key="private-test-key", timeout_seconds=9)
    papers = backend.search("learning rate", max_results=5, start_year=2019, end_year=2024)
    assert len(papers) == 1
    assert papers[0] == LiteraturePaper(
        paper_id="openalex:W123", title="A real-shaped record", authors=["Alice"],
        published_at=date(2020, 1, 2), abstract="Retrieved abstract evidence.",
        source_url="https://openalex.org/W123",
    )
    request, timeout = requests[0]
    params = parse_qs(urlsplit(str(request.url)).query)
    assert params["search"] == ["learning rate"]
    assert params["per_page"] == ["5"]
    assert params["filter"] == ["from_publication_date:2019-01-01,to_publication_date:2024-12-31"]
    assert request.headers.get("Authorization") == "Bearer private-test-key"
    assert request.headers.get("User-agent").startswith("ResAgent2/")
    assert "private-test-key" not in str(request.url)
    assert "private-test-key" not in papers[0].model_dump_json()
    assert timeout == 9


def test_optional_metadata_and_key_can_be_absent(monkeypatch):
    record = work()
    record.update(publication_date=None, authorships=[], abstract_inverted_index=None)
    requests = fake_response(monkeypatch, json.dumps({"results": [record]}).encode())
    paper = OpenAlexLiteratureBackend().search("x", max_results=1)[0]
    assert paper.abstract == ""
    assert paper.authors == []
    assert paper.published_at is None
    assert requests[0][0].headers.get("Authorization") is None
    assert "filter" not in parse_qs(urlsplit(str(requests[0][0].url)).query)


def test_abstract_bound_matches_arxiv():
    backend = OpenAlexLiteratureBackend(max_abstract_chars=10)
    assert backend._paper(work()).abstract == "Retrieved "


@pytest.mark.parametrize("body", [
    b"not json", b"[]", b"{}", b'{"results": null}', b'{"results": [{}]}',
    b'{"results": [null]}',
])
def test_invalid_response_is_not_empty_success(monkeypatch, body):
    fake_response(monkeypatch, body)
    with pytest.raises(LiteratureSearchError, match="invalid bibliographic data"):
        OpenAlexLiteratureBackend().search("x", max_results=1)


@pytest.mark.parametrize("index", [[], {"word": "1"}, {"word": [-1]}, {"word": [True]}, {"a": [0], "b": [0]}])
def test_invalid_abstract_does_not_fabricate_evidence(index):
    record = work()
    record["abstract_inverted_index"] = index
    with pytest.raises(LiteratureSearchError):
        OpenAlexLiteratureBackend()._parse(json.dumps({"results": [record]}).encode())


def test_valid_empty_result(monkeypatch):
    fake_response(monkeypatch, b'{"results":[]}')
    assert OpenAlexLiteratureBackend().search("x", max_results=1) == []


class Failing:
    def __init__(self, error):
        self.error = error
    def search(self, query, **kwargs):
        raise self.error


def test_source_switch_preserves_bounds_and_actual_source_in_artifact(caplog, tmp_path):
    paper = OpenAlexLiteratureBackend()._paper(work())
    other = _FakeBackend([paper])
    backend = MultiSourceLiteratureBackend(Failing(LiteratureUnavailableError("arXiv HTTP 429")), other)
    register = _FakeRegister(tmp_path)
    observation = LiteratureSearchTool(backend, register).execute(
        state(), LiteratureSearchToolInput(query="x", max_results=2, start_year=2020, end_year=2024),
    )
    assert other.last_kwargs == dict(query="x", max_results=2, start_year=2020, end_year=2024)
    assert "arXiv HTTP 429" in caplog.text
    from resagent2_components import RegisteredArtifactReader
    paper_ref = register.refs[observation.value["papers"][0]["artifact_id"]]
    reader = RegisteredArtifactReader(list(register.refs.values()), run_id=state().run_id)
    body = reader.read_text(paper_ref.id)["content"]
    assert paper_ref.media_type == "text/markdown"
    assert "https://openalex.org/W123" in body
    assert "not paper full text" in body
    assert paper_ref.metadata["paper"]["paper_id"] == "openalex:W123"
    assert observation.value["papers"][0]["source_url"] == paper.source_url
    receipt = json.loads(register.last_candidate.content)
    assert receipt["paper_artifact_ids"] == [paper_ref.id]
    assert receipt["status"] == "results"


def test_empty_result_does_not_trigger_source_switch():
    other = _FakeBackend([])
    assert MultiSourceLiteratureBackend(_FakeBackend([]), other).search("x", max_results=1) == []
    assert other.last_kwargs is None


@pytest.mark.parametrize("error", [LiteratureSearchError("HTTP 400"), LiteratureSearchError("invalid XML"), RuntimeError("bug")])
def test_non_availability_errors_do_not_trigger_source_switch(error):
    other = _FakeBackend([])
    with pytest.raises(type(error), match=str(error)):
        MultiSourceLiteratureBackend(Failing(error), other).search("x", max_results=1)
    assert other.last_kwargs is None


def test_both_sources_failing_records_failure_without_registering_papers(tmp_path):
    backend = MultiSourceLiteratureBackend(
        Failing(LiteratureUnavailableError("arXiv HTTP 429")),
        Failing(LiteratureUnavailableError("OpenAlex TimeoutError")),
    )
    register = _FakeRegister(tmp_path)
    result = LiteratureSearchTool(backend, register).execute(state(), LiteratureSearchToolInput(query="x"))
    assert not result.ok
    assert result.value["status"] == "failed"
    assert "arXiv HTTP 429" in result.value["error"]
    assert "OpenAlex TimeoutError" in result.value["error"]
    assert {ref.kind for ref in register.refs.values()} == {"literature_search"}
    receipt = json.loads(register.last_candidate.content)
    assert receipt["paper_artifact_ids"] == []
    assert receipt["status"] == "failed"
    assert receipt["error"] == result.value["error"]


@pytest.mark.parametrize("remaining,reset,retry_after,expected", [
    ("0", "3600", None, 3600),
    ("0.0", "90", "120", 120),
    ("0", "30", None, 60),
    ("1", "3600", None, 60),
    ("0.5", "3600", None, 60),
    (None, "3600", None, 60),
    ("-1", "3600", None, 60),
    ("NaN", "3600", None, 60),
    ("1e-999", "3600", None, 60),
    ("0", None, None, 60),
    ("0", "0", None, 60),
    ("0", "-1", None, 60),
    ("0", "NaN", None, 60),
    ("0", "inf", None, 60),
    ("0", "unknown", None, 60),
    ("0", "1" * 100, None, 60),
])
def test_429_only_explicit_exhausted_quota_extends_cooldown(
    monkeypatch, caplog, remaining, reset, retry_after, expected,
):
    from tests.components.test_literature_http import response_records

    headers = {
        key: value for key, value in {
            "X-RateLimit-Remaining": remaining,
            "X-RateLimit-Reset": reset,
            "Retry-After": retry_after,
        }.items() if value is not None
    }
    calls = []
    def open_request(request, *, timeout):
        calls.append(request)
        response = httpx.Response(429, request=request, headers=headers)
        response.raise_for_status()

    monkeypatch.setattr(openalex, "send_request", open_request)
    backend = OpenAlexLiteratureBackend()
    with pytest.raises(LiteratureUnavailableError, match="429"):
        backend.search("calibration", max_results=3)
    assert openalex._OPENALEX_HTTP._cooldown_until == expected
    assert response_records(caplog)[-1]["cooldown_seconds"] == expected
    with pytest.raises(LiteratureUnavailableError, match="cooling down"):
        backend.search("other keywords", max_results=3)
    assert len(calls) == 1


def test_exhausted_openalex_quota_does_not_block_peer_search(monkeypatch):
    def open_request(request, *, timeout):
        httpx.Response(429, request=request, headers={
            "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600",
        }).raise_for_status()

    monkeypatch.setattr(openalex, "send_request", open_request)
    peer = _FakeBackend([OpenAlexLiteratureBackend()._paper(work())])
    backend = MultiSourceLiteratureBackend(OpenAlexLiteratureBackend(), peer)
    assert len(backend.search("calibration", max_results=3)) == 1
    assert openalex._OPENALEX_HTTP._cooldown_until == 3600
    assert peer.last_kwargs["query"] == "calibration"


def test_quota_reset_does_not_change_non_429_retries(monkeypatch):
    calls = []
    def open_request(request, *, timeout):
        calls.append(request)
        response = httpx.Response(503, request=request, headers={
            "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600",
        })
        response.raise_for_status()

    monkeypatch.setattr(openalex, "send_request", open_request)
    with pytest.raises(LiteratureUnavailableError, match="after 3 attempts"):
        OpenAlexLiteratureBackend().search("calibration", max_results=3)
    assert len(calls) == 3
    assert openalex._OPENALEX_HTTP._cooldown_until == 69
