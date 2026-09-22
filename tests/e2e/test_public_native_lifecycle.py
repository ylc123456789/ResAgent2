"""Public CLI -> native Agents -> Compiler -> answers -> final report, without a model service."""

import hashlib
import io
import json
import subprocess
from collections import Counter

import httpx

from resagent2_cli.main import EXIT_COMPLETED, EXIT_PAUSED, cli
from resagent2_cli.shell import Shell
from resagent2_cli.composition import build_application
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import JsonRunStore
from resagent2_orchestrator.handoffs import read_json


def test_cli_rebuilds_and_finishes_two_native_work_rounds(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    obsolete = repo / "obsolete"
    obsolete.mkdir()
    (obsolete / "old.py").write_text("OLD = True\n")
    metrics = repo / "metrics.json"
    metrics.write_text('{"baseline": 0.45, "candidate": 0.52}')
    original_hash = hashlib.sha256(metrics.read_bytes()).hexdigest()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    data = tmp_path / "data"
    store = JsonRunStore(data / "state")
    run_id = "run_public_chain"
    counts = Counter()
    requests = []
    monkeypatch.setenv("RESAGENT2_MODEL", "chain-test")
    monkeypatch.setenv("RESAGENT2_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("RESAGENT2_API_KEY_ENV", "CHAIN_TEST_KEY")
    monkeypatch.setenv("CHAIN_TEST_KEY", "test-only")
    monkeypatch.setenv("RESAGENT2_LLM_TRACE_LEVEL", "off")
    monkeypatch.setenv("RESAGENT2_RESOURCE_ROOT", str(tmp_path / "resources"))

    def evidence_id():
        run = store.load(run_id)
        return next(ref.id for ref in run.artifacts.values() if ref.kind == "metrics")

    def work(objective):
        return "request_work", {
            "assessment": {"statement": objective},
            "work_request": {"objective": objective, "expected_evidence": ["Recorded outcome"]},
        }

    def reply(request, **kwargs):
        body = json.loads(request.content)
        requests.append(body)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        role = ("compiler" if not names else "scientific" if "request_work" in names
                else "coding" if "delete_path" in names else "experiment")
        counts[role] += 1
        index = counts[role]
        if role == "compiler":
            assert index <= 2, body
            prompt = "\n".join(message.get("content") or "" for message in body["messages"])
            assert NativeCodingAgent.description in prompt
            assert NativeExperimentAgent.description in prompt
            assert "upper bound, not a target" in prompt
            assert "not execution" in prompt
            kind = "coding" if index == 1 else "experiment"
            content = json.dumps({"tasks": [{
                "key": kind, "workflow_agent_kind": kind, "workspace_id": "ws_main",
                "instruction": "Remove obsolete recursively" if index == 1 else "Analyze metrics.json; ask which metric",
            }]})
            message = {"content": content}
            finish = "stop"
        else:
            if role == "scientific":
                if index == 1:
                    tool, args = work("Remove the obsolete source directory with approval")
                elif index == 2:
                    tool, args = work("Analyze existing baseline and candidate metrics; ask which metric")
                elif index == 3:
                    tool, args = "read_artifact", {"artifact_id": evidence_id()}
                elif index in (4, 5):
                    if index == 5:
                        current = body["messages"][-1]["content"]
                        assert "runtime_feedback" in current and "data" in current
                        assert store.load(run_id).status != "failed"
                    tool, args = "finish", {"report": "The recorded candidate exceeds baseline by 0.07",
                        "artifacts": [{
                            "kind": "scientific_opinion", "path": "opinion.json",
                            "media_type": "application/json", "summary": "Analysis of existing measurements",
                            "content": json.dumps({
                                "verdict": "inconclusive", "statement": "Recorded accuracy difference is 0.07",
                                "evidence_artifact_ids": [evidence_id()],
                                "limitations": ["Existing results only; no new experiment executed"],
                            }),
                        }]}
                    if index == 4:
                        # Same error as the real server run: trying to deliver input
                        # evidence again. Reject inside the Agent so it can correct it.
                        args["artifacts"].insert(0, {
                            "kind": "data", "path": evidence_id(),
                            "media_type": "application/json", "summary": "Existing input evidence",
                        })
                else:
                    raise AssertionError(body)
            elif role == "coding":
                if index in (1, 2):
                    tool, args = "delete_path", {"path": "obsolete", "recursive": True}
                elif index == 3:
                    tool, args = "finish", {"report": "Removed obsolete source"}
                else:
                    raise AssertionError(body)
            else:
                if index == 1:
                    tool, args = "ask_user", {"text": "Which metric should be primary?", "requested_fields": ["metric"]}
                elif index == 2:
                    tool, args = "read_file", {"path": "metrics.json"}
                elif index == 3:
                    tool, args = "finish", {"report": "Accuracy difference is 0.07", "artifacts": [{
                        "kind": "metrics", "path": "metrics.json", "media_type": "application/json",
                        "summary": "Existing baseline and candidate values",
                    }]}
                else:
                    raise AssertionError(body)
            message = {"content": None, "tool_calls": [{
                "id": f"call_{role}_{index}", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args)},
            }]}
            finish = "tool_calls"
        return httpx.Response(200, request=request, json={"choices": [{
            "finish_reason": finish, "message": message,
        }]})

    monkeypatch.setattr("resagent2_runtime.llm.send_request", reply)
    assert cli([
        "run", "--run-id", run_id, "--goal", "Clean obsolete source and analyze existing accuracy",
        "--workspace", str(repo), "--data-root", str(data),
        "--no-execute-commands", "--no-prepare-environment",
        "--max-llm-calls", "30", "--max-tasks", "2",
    ]) == EXIT_PAUSED
    first = store.load(run_id)
    assert obsolete.is_dir()
    assert first.pending_question.action.tool == "delete_path"
    coding_session = first.workflow.tasks[0].attempts[0].session.id
    scientific_session = first.scientific_session.id

    # Shell uses a fresh production application and the same controller answer API.
    shell = Shell(data_root=data, application_builder=build_application, store=store, stream=io.StringIO())
    shell.current_run_id = run_id
    shell._dispatch("/answer yes")
    assert shell.runner.done
    second = store.load(run_id)
    assert not obsolete.exists()
    assert second.status == "paused"
    assert second.pending_question.requested_fields == ["metric"]
    assert second.workflow.revision == 2
    assert second.workflow.tasks[0].attempts[0].session.id == coding_session
    experiment_session = second.workflow.tasks[1].attempts[0].session.id
    used_before_answer = second.llm_calls_used

    # The one-shot CLI rebuilds the entire application again from durable state.
    assert cli(["answer", run_id, "--field", "metric=accuracy", "--data-root", str(data)]) == EXIT_COMPLETED
    final = store.load(run_id)
    assert final.status == "completed", final.terminal_error
    assert final.pending_question is None
    assert final.scientific_session.id == scientific_session
    assert final.workflow.tasks[1].attempts[0].session.id == experiment_session
    assert all(len(task.attempts) == 1 and task.status == "completed" for task in final.workflow.tasks)
    assert all(work.status == "consumed" for work in final.work_requests)
    assert final.llm_calls_used == len(requests) == sum(counts.values())
    assert final.llm_calls_used > used_before_answer
    assert set(counts) == {"scientific", "compiler", "coding", "experiment"}
    assert counts["scientific"] == 5  # Rejected finish and correction share the same budget.
    assert final.usage.outcomes == {"succeeded": len(requests), "failed": 0, "unknown": 0}
    assert len(final.answers) == 2
    answers = [read_json(ref) for ref in final.artifacts.values() if ref.kind == "answer"]
    assert {answer["question_text"] for answer in answers} == {first.pending_question.text, second.pending_question.text}
    assert final.final_opinion.evidence_artifact_ids == [evidence_id()]
    assert evidence_id() in final.scientific_observed_artifact_ids
    assert final.artifacts[final.final_report_artifact_id].kind == "final_report"
    assert hashlib.sha256(metrics.read_bytes()).hexdigest() == original_hash
    assert not any(ref.kind == "execution_record" for ref in final.artifacts.values())
