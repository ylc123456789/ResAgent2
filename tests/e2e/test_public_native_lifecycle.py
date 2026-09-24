"""Public CLI -> native Agents -> Compiler/Interpreter -> answers -> final report."""

import hashlib
import io
import json
import subprocess
from collections import Counter

import httpx

from resagent2_cli.main import EXIT_COMPLETED, EXIT_PAUSED, cli
from resagent2_cli.shell import Shell
from resagent2_cli.composition import build_application
from resagent2_components import RegisteredArtifactReader
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import JsonRunStore
from resagent2_orchestrator.handoffs import read_json
from resagent2_scientific import ScientificAgent


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
    scientific_requests = []
    round_answer_ids = {}
    handoff_indexes = {}
    monkeypatch.setenv("RESAGENT2_MODEL", "chain-test")
    monkeypatch.setenv("RESAGENT2_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("RESAGENT2_API_KEY_ENV", "CHAIN_TEST_KEY")
    monkeypatch.setenv("CHAIN_TEST_KEY", "test-only")
    monkeypatch.setenv("RESAGENT2_LLM_TRACE_LEVEL", "off")
    monkeypatch.setenv("RESAGENT2_RESOURCE_ROOT", str(tmp_path / "resources"))

    invoke_scientific = ScientificAgent.invoke

    def capture_scientific(self, request):
        scientific_requests.append(request)
        return invoke_scientific(self, request)

    monkeypatch.setattr(ScientificAgent, "invoke", capture_scientific)

    def answer_ref(field):
        return next(ref for ref in store.load(run_id).artifacts.values()
                    if ref.kind == "answer" and field in read_json(ref)["requested_fields"])

    def assert_answer_read(body, call_id, field):
        receipt = json.loads(next(message["content"] for message in body["messages"]
                                  if message.get("tool_call_id") == call_id))
        ref = answer_ref(field)
        assert receipt["ok"] is True
        assert receipt["value"]["artifact_id"] == ref.id
        assert json.loads(receipt["value"]["content"]) == read_json(ref)

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
        prompt = "\n".join(message.get("content") or "" for message in body["messages"])
        role = ("interpreter" if not names and "stateless ResAgent2 Work Interpreter" in prompt
                else "compiler" if not names else "scientific" if "request_work" in names
                else "coding" if "delete_path" in names else "experiment")
        counts[role] += 1
        index = counts[role]
        if role == "compiler":
            assert index <= 2, body
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
        elif role == "interpreter":
            assert index <= 2, body
            payloads = [json.loads(line) for line in prompt.splitlines() if line.startswith("{")]
            payload = next(value for value in payloads if "work_record_artifact_id" in value)
            record_id = payload["work_record_artifact_id"]
            record = payload["work_record"]
            assert record["run_id"] == run_id
            assert record["work_request_id"] == f"work_{index}"
            assert record["attempts"][0]["status"] == "completed"
            assert all(source["artifact_id"] in {
                entry["artifact_id"]
                for group in payload["research_index"]["groups"]
                for entry in group["artifacts"]
            } for source in payload["source_windows"])
            answer_sources = [source for source in payload["source_windows"] if source["kind"] == "answer"]
            assert len(answer_sources) == 1
            answer_source = answer_sources[0]
            expected_answer = answer_ref("approve" if index == 1 else "metric")
            assert answer_source["artifact_id"] == expected_answer.id
            assert json.loads(answer_source["content"]) == read_json(expected_answer)
            round_answer_ids[index] = expected_answer.id
            if index == 2:
                # The full Scientific directory retains round 1, but the brief's
                # sources stay within this round and its explicitly supplied inputs.
                assert "work_1" not in {group["key"] for group in payload["research_index"]["groups"]}
                assert round_answer_ids[1] not in {
                    source["artifact_id"] for source in payload["source_windows"]
                }
                metrics_source = next(
                    source for source in payload["source_windows"] if source["artifact_id"] == evidence_id()
                )
                assert json.loads(metrics_source["content"]) == {"baseline": 0.45, "candidate": 0.52}
            message = {"content": json.dumps({"statements": [{
                "text": f"INTERPRETED_ROUND_{index}: the requested work completed; inspect its recorded results.",
                "artifact_ids": [record_id, expected_answer.id],
            }]})}
            finish = "stop"
        else:
            if role == "scientific":
                current_context = body["messages"][-1]["content"]
                payloads = [json.loads(line) for line in current_context.splitlines() if line.startswith("{")]
                materials = next(value for value in payloads if "index_artifact_id" in value and "index" in value)
                current_request = scientific_requests[-1]
                reader = RegisteredArtifactReader(current_request.input_artifacts, run_id=run_id)
                entries = [entry for group in materials["index"]["groups"] for entry in group["artifacts"]]
                indexed_ids = {entry["artifact_id"] for entry in entries}
                # Verify every rendered directory entry against precisely the
                # references delivered to this real Scientific invocation.
                for artifact_id in indexed_ids:
                    assert reader.read_text(artifact_id)["artifact_id"] == artifact_id
                if index in (2, 3):
                    handoff_indexes[index - 1] = materials["index"]
                    assert materials["index"] == json.loads(reader.read_text(materials["index_artifact_id"])["content"])
                    assert round_answer_ids[index - 1] in indexed_ids
                if index == 1:
                    tool, args = work("Remove the obsolete source directory with approval")
                elif index == 2:
                    assert "INTERPRETED_ROUND_1" in prompt
                    tool, args = work("Analyze existing baseline and candidate metrics; ask which metric")
                elif index == 3:
                    assert "INTERPRETED_ROUND_2" in prompt
                    previous_ids = {entry["artifact_id"] for group in handoff_indexes[1]["groups"]
                                    for entry in group["artifacts"]}
                    assert previous_ids <= indexed_ids
                    assert set(round_answer_ids.values()) <= indexed_ids
                    assert {"work_1", "work_2"} <= {group["key"] for group in materials["index"]["groups"]}
                    # Interpreter reads do not satisfy Scientific's own evidence observation.
                    assert evidence_id() not in store.load(run_id).scientific_observed_artifact_ids
                    tool, args = "read_artifact", {"artifact_id": evidence_id()}
                elif index == 4:
                    tool, args = "read_artifact", {"artifact_id": answer_ref("approve").id}
                elif index == 5:
                    assert_answer_read(body, "call_scientific_4", "approve")
                    tool, args = "read_artifact", {"artifact_id": answer_ref("metric").id}
                elif index in (6, 7):
                    if index == 6:
                        assert_answer_read(body, "call_scientific_5", "metric")
                    if index == 7:
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
                    if index == 6:
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
    first_feedback = second.feedback_refs["work_1"]
    assert counts["interpreter"] == 1

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
    assert set(counts) == {"scientific", "compiler", "coding", "experiment", "interpreter"}
    assert counts["interpreter"] == 2
    # Two original paired-answer reads, plus the rejected finish and its correction.
    assert counts["scientific"] == 7
    assert len(scientific_requests) == 3
    assert final.usage.outcomes == {"succeeded": len(requests), "failed": 0, "unknown": 0}
    assert len(final.answers) == 2
    answers = [read_json(ref) for ref in final.artifacts.values() if ref.kind == "answer"]
    assert {answer["question_text"] for answer in answers} == {first.pending_question.text, second.pending_question.text}
    assert final.final_opinion.evidence_artifact_ids == [evidence_id()]
    assert {evidence_id(), *round_answer_ids.values()} <= set(final.scientific_observed_artifact_ids)
    assert final.artifacts[final.final_report_artifact_id].kind == "final_report"
    assert hashlib.sha256(metrics.read_bytes()).hexdigest() == original_hash
    assert not any(ref.kind == "execution_record" for ref in final.artifacts.values())

    assert final.feedback_refs["work_1"] == first_feedback
    assert set(final.feedback_refs) == {"work_1", "work_2"}
    for round_number, feedback_ref in enumerate(final.feedback_refs.values(), start=1):
        feedback = read_json(feedback_ref)
        assert feedback["brief"]["statements"] == [{
            "schema_version": feedback["schema_version"],
            "text": f"INTERPRETED_ROUND_{round_number}: the requested work completed; inspect its recorded results.",
            "artifact_ids": [feedback["work_record_artifact_id"], round_answer_ids[round_number]],
        }]
        assert "index_changes" not in feedback
        snapshot = read_json(final.artifacts[feedback["index_artifact_id"]])
        assert snapshot == handoff_indexes[round_number]
        groups = {group["key"]: group for group in snapshot["groups"]}
        for previous_round in range(1, round_number + 1):
            ids = {entry["artifact_id"] for entry in groups[f"work_{previous_round}"]["artifacts"]}
            assert round_answer_ids[previous_round] in ids
            assert read_json(final.feedback_refs[f"work_{previous_round}"])["work_record_artifact_id"] in ids
        indexed_ids = {entry["artifact_id"] for group in groups.values() for entry in group["artifacts"]}
        assert set(feedback["brief"]["statements"][0]["artifact_ids"]) <= indexed_ids
        assert final.artifacts[feedback["index_artifact_id"]].kind == "research_index"
        assert final.artifacts[feedback["work_record_artifact_id"]].kind == "work_record"

    index = read_json(final.research_index_ref)
    assert {"work_1", "work_2"} <= {group["key"] for group in index["groups"]}
    indexed_ids = {
        entry["artifact_id"] for group in index["groups"] for entry in group["artifacts"]
    }
    assert {evidence_id(), *round_answer_ids.values()} <= indexed_ids
    assert read_json(first_feedback)["work_record_artifact_id"] in indexed_ids
