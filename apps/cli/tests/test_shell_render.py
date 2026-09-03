"""Pure renderer tests: state -> lines, no terminal, no LLM."""

from __future__ import annotations

from types import SimpleNamespace

from resagent2_cli.render import (
    render_artifacts,
    render_final,
    render_live,
    render_trace,
)


def _run(goal="g", status="running", llm_used=5, llm_max=200):
    return SimpleNamespace(
        run_id="run_x",
        status=SimpleNamespace(value=status),
        request=SimpleNamespace(
            goal=goal, budget=SimpleNamespace(max_llm_calls=llm_max)
        ),
        llm_calls_used=llm_used,
        workflow=None,
        latest_scientific_assessment=None,
        pending_question=None,
        artifacts={},
        final_opinion=None,
        completion_violations=[],
    )


def test_render_live_none():
    assert render_live(None) == ["… starting …"]


def test_render_live_header_and_pending_question():
    run = _run()
    run.pending_question = SimpleNamespace(
        text="pick the primary metric", requested_fields=["primary_metric"]
    )
    joined = "\n".join(render_live(run, None))
    assert "run_x" in joined
    assert "running" in joined
    assert "pick the primary metric" in joined
    assert "primary_metric" in joined


def test_render_live_shows_last_tool_activity():
    run = _run()
    lines = render_live(run, [{"agent": "coding", "tool": "read_file", "step": 3}])
    assert "→ coding/read_file (step 3)" in lines


def test_render_live_task_attempts_and_error():
    task = SimpleNamespace(
        id="task_1",
        capability=SimpleNamespace(value="code_modify"),
        status=SimpleNamespace(value="failed"),
        goal="fix the SE bug",
        attempts=[
            SimpleNamespace(
                number=1,
                status=SimpleNamespace(value="failed"),
                summary="editing finished",
                error=SimpleNamespace(
                    code=SimpleNamespace(value="TOOL_FAILED"),
                    message="boom",
                ),
            )
        ],
    )
    run = _run()
    run.workflow = SimpleNamespace(tasks=[task])
    joined = "\n".join(render_live(run, None))
    assert "task_1" in joined
    assert "code_modify" in joined
    assert "failed" in joined
    assert "attempt 1" in joined
    assert "editing finished" in joined
    assert "TOOL_FAILED: boom" in joined


def test_render_final_basic_summary():
    run = _run(goal="my goal", status="completed")
    joined = "\n".join(render_final(run))
    assert "Run: run_x" in joined
    assert "Status: completed" in joined
    assert "Goal: my goal" in joined
    assert "LLM calls: 5/200" in joined


def test_render_final_pending_question_fields():
    run = _run(status="paused")
    run.pending_question = SimpleNamespace(
        text="choose", requested_fields=["metric", "seed"]
    )
    joined = "\n".join(render_final(run))
    assert "Pending question:" in joined
    assert "Fields: metric, seed" in joined


def test_render_artifacts_empty():
    assert render_artifacts(_run()) == ["No artifacts."]


def test_render_artifacts_listing():
    run = _run()
    run.artifacts = {
        "artifact_1": SimpleNamespace(
            id="artifact_1",
            kind="experiment_result",
            producer=SimpleNamespace(value="experiment"),
            uri="file:///tmp/x.json",
        )
    }
    joined = "\n".join(render_artifacts(run))
    assert "Artifacts (1):" in joined
    assert "artifact_1: experiment_result [experiment] file:///tmp/x.json" in joined


def test_render_trace_empty():
    assert render_trace([]) == ["No trace records."]


def test_render_trace_full_level():
    records = [
        {
            "sequence": 1,
            "agent": "scientific",
            "tool": "finish",
            "step": 2,
            "request_text": "REQ",
            "raw_response_text": "RESP",
            "raw_reasoning_text": "THINK",
        }
    ]
    joined = "\n".join(render_trace(records))
    assert "agent=scientific" in joined
    assert "tool=finish" in joined
    assert "[request]" in joined and "REQ" in joined
    assert "[reasoning]" in joined and "THINK" in joined
    assert "[response]" in joined and "RESP" in joined
