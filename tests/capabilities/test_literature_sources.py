"""Source selection has no arXiv/OpenAlex special cases or network access."""

import pytest

from resagent2_capabilities import (
    LiteratureSearchError,
    LiteratureUnavailableError,
    MultiSourceLiteratureBackend,
)
from tests.capabilities.test_literature import paper


class ScriptedSource:
    def __init__(self, name, replies, calls):
        self.name = name
        self.replies = iter(replies)
        self.calls = calls

    def search(self, query, **bounds):
        self.calls.append((self.name, query, bounds))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.parametrize("names", [("arxiv", "openalex"), ("openalex", "arxiv")])
def test_peers_can_switch_both_ways_and_reuse_success(names):
    calls = []
    first_result, second_result = [paper("1")], [paper("2")]
    first = ScriptedSource(names[0], [
        LiteratureUnavailableError("rate limited"), first_result,
    ], calls)
    second = ScriptedSource(names[1], [
        second_result, second_result, LiteratureUnavailableError("rate limited"),
    ], calls)
    backend = MultiSourceLiteratureBackend(first, second)
    bounds = dict(max_results=4, start_year=2020, end_year=2024)

    assert backend.search("first", **bounds) == second_result
    assert backend.search("next", **bounds) == second_result
    # The previous source can recover and be used when the current one fails.
    assert backend.search("return", **bounds) == first_result
    assert [(name, query) for name, query, _ in calls] == [
        (names[0], "first"), (names[1], "first"),
        (names[1], "next"),
        (names[1], "return"), (names[0], "return"),
    ]
    assert all(received == bounds for _, _, received in calls)


def test_all_unavailable_visits_each_source_once_and_keeps_all_errors():
    calls = []
    sources = [
        ScriptedSource(name, [LiteratureUnavailableError(name)], calls)
        for name in ("first", "second", "third")
    ]
    with pytest.raises(LiteratureUnavailableError, match="All literature sources") as caught:
        MultiSourceLiteratureBackend(*sources).search("x", max_results=1)
    assert [name for name, _, _ in calls] == ["first", "second", "third"]
    assert all(name in str(caught.value) for name in ("first", "second", "third"))


def test_initial_order_is_configurable_and_success_does_not_query_other_sources():
    calls = []
    expected = [paper("1")]
    selected = ScriptedSource("selected", [expected, expected], calls)
    untouched = ScriptedSource("untouched", [], calls)
    backend = MultiSourceLiteratureBackend(selected, untouched)
    assert backend.search("x", max_results=1) == expected
    assert backend.search("y", max_results=1) == expected
    assert [name for name, _, _ in calls] == ["selected", "selected"]


def test_valid_empty_result_after_switch_is_success_not_another_failure():
    calls = []
    first = ScriptedSource("first", [LiteratureUnavailableError("offline")], calls)
    second = ScriptedSource("second", [[], []], calls)
    backend = MultiSourceLiteratureBackend(first, second)
    assert backend.search("x", max_results=1) == []
    assert backend.search("y", max_results=1) == []
    assert [name for name, _, _ in calls] == ["first", "second", "second"]


def test_request_error_does_not_loop_back_or_try_more_sources():
    calls = []
    first = ScriptedSource("first", [LiteratureUnavailableError("offline")], calls)
    second = ScriptedSource("second", [LiteratureSearchError("HTTP 400")], calls)
    third = ScriptedSource("third", [], calls)
    with pytest.raises(LiteratureSearchError, match="HTTP 400"):
        MultiSourceLiteratureBackend(first, second, third).search("x", max_results=1)
    assert [name for name, _, _ in calls] == ["first", "second"]


def test_empty_configuration_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        MultiSourceLiteratureBackend()


def test_peer_switching_respects_each_real_backends_http_cooldown(monkeypatch):
    import json
    from urllib.error import HTTPError
    from resagent2_capabilities import (
        _literature_http,
        literature,
        openalex,
    )
    from tests.capabilities.test_literature import ARXIV_ATOM
    from tests.capabilities.test_openalex import work

    clock, calls = [0.0], []
    monkeypatch.setattr(_literature_http.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        _literature_http.time, "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    for module, attribute, name, interval in (
        (literature, "_ARXIV_HTTP", "arXiv", 3),
        (openalex, "_OPENALEX_HTTP", "OpenAlex", 1),
    ):
        monkeypatch.setattr(
            module, attribute,
            _literature_http.LiteratureHTTP(name, interval_seconds=interval),
        )

    def arxiv_request(self, url):
        calls.append(("arxiv", clock[0]))
        if clock[0] < 60:
            raise HTTPError(url, 429, "rate limited", {}, None)
        return ARXIV_ATOM.encode()

    def openalex_request(self, url):
        calls.append(("openalex", clock[0]))
        if clock[0] >= 10:
            raise HTTPError(url, 429, "rate limited", {}, None)
        return json.dumps({"results": [work()]}).encode()

    monkeypatch.setattr(literature.ArxivLiteratureBackend, "_request", arxiv_request)
    monkeypatch.setattr(openalex.OpenAlexLiteratureBackend, "_request", openalex_request)
    backend = MultiSourceLiteratureBackend(
        literature.ArxivLiteratureBackend(), openalex.OpenAlexLiteratureBackend(),
    )
    assert backend.search("x", max_results=3)[0].paper_id == "openalex:W123"
    clock[0] = 10
    with pytest.raises(LiteratureUnavailableError, match="All literature sources"):
        backend.search("x", max_results=3)
    # arXiv remains in cooldown: do not make another HTTP request to it yet.
    assert calls == [("arxiv", 0), ("openalex", 0), ("openalex", 10)]
    clock[0] = 61
    assert backend.search("x", max_results=3)[0].paper_id == "2301.00001"
    # OpenAlex remains in cooldown until t=70; arXiv has recovered.
    assert calls[-1] == ("arxiv", 61)
    assert len(calls) == 4
