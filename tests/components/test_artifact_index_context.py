"""Complete authorization-scoped navigation, without material-body duplication."""
import json
from datetime import UTC,datetime
import pytest
from resagent2_contracts import AgentRequest, AgentPermissions, ArtifactRef,TaskBudget,WorkResult,WorkTaskResult,WorkAttemptResult
from resagent2_runtime import AgentState, ContextComposer
from resagent2_runtime.context import ContextBudgetExceeded
from resagent2_components import RegisteredArtifactReader,artifact_index_context
from resagent2_components.artifacts import ArtifactReadError
NOW=datetime.now(UTC)


def ref(n,**kw):
    data=dict(id=f"artifact_{n}",kind="web_page",producer="scientific",run_id="run_test",session_id="session_test",
        uri=f"file:///private/{n}",sha256="a"*64,summary=f"Material {n}",media_type="text/markdown")
    return ArtifactRef(**(data|kw))


def request(refs,**kw):
    return AgentRequest(run_id="run_test",agent="scientific",instruction="Inspect materials",input_artifacts=refs,
        permissions=AgentPermissions(),budget=TaskBudget(max_llm_calls=4,timeout_seconds=60),**kw)


def state(ids=()):
    return AgentState(session_id="session_test",agent_name="scientific",owner="scientific",run_id="run_test",
        created_at=NOW,updated_at=NOW,authorized_artifact_ids=list(ids))


def test_complete_directory_resolves_only_accepted_ids_and_keeps_all_entries():
    refs=[ref(i) for i in range(80)]
    req=request(refs[:2]); current=state([r.id for r in refs[1:]])
    reader=RegisteredArtifactReader(refs,run_id=req.run_id)
    before=current.model_dump_json()
    section=artifact_index_context(req,current,reader=reader)
    index=json.loads(section.content)
    assert index["artifact_count"]==80
    assert [e["artifact_id"] for e in index["materials"]["web"]]==[r.id for r in refs]
    assert "/private/" not in section.content and "a"*64 not in section.content
    assert current.model_dump_json()==before
    with pytest.raises(ContextBudgetExceeded):
        ContextComposer().compose("System",[section],max_tokens=30)


def test_directory_missing_accepted_ref_fails_instead_of_silently_omitting():
    with pytest.raises(ArtifactReadError,match="unavailable"):
        artifact_index_context(request([]),state(["artifact_missing"]))


def test_work_task_original_reports_and_outputs_are_expanded_once():
    report=ref("report",kind="module_report",producer="orchestrator",session_id=None,
        task_id="task_code",attempt_number=1,metadata={"source_type":"task_report"})
    output=ref("code",kind="code",producer="coding",session_id=None,task_id="task_code",attempt_number=1)
    paper=ref("paper",kind="literature_paper")
    work=WorkResult(run_id="run_test",session_id="session_test",work_request_id="work_one",objective="Implement",
        status="stable",workflow_revision=1,artifacts=[report,output,paper],tasks=[WorkTaskResult(
            task_id="task_code",workflow_agent_kind="coding",status="completed",instruction="Implement the method",
            attempts=[WorkAttemptResult(number=1,status="completed",report_artifact_id=report.id,artifact_ids=[output.id])])])
    req=request([paper,report,output],parent_session_id="session_test",work_results=[work])
    value=json.loads(artifact_index_context(req,state()).content)
    task=value["work_requests"][0]["tasks"][0]
    assert task["instruction"]=="Implement the method"
    assert [e["artifact_id"] for e in task["artifacts"]]==[report.id,output.id]
    assert [e["artifact_id"] for e in value["materials"]["literature"]]==[paper.id]
    assert value["work_requests"][0]["referenced_artifact_ids"]==[paper.id]
    assert value["artifact_count"]==3


def test_directory_errors_keep_navigation_without_copying_diagnostic_bodies():
    from resagent2_contracts import ModuleError, WarningRecord

    error = ModuleError(code="tool_failed", message="Tool execution failed", retryable=False,
                        details={"last_observation": {"content": "BODY_ONLY_IN_TOOL_HISTORY", "uri": "file:///private/result"}})
    warning = WarningRecord(code="incomplete", message="Some evidence is missing", details=error.details)
    work = WorkResult(
        run_id="run_test", session_id="session_test", work_request_id="work_failed", objective="Measure",
        status="failed", error=error, tasks=[WorkTaskResult(
            task_id="task_failed", workflow_agent_kind="experiment", status="failed", instruction="Measure",
            error=error, warnings=[warning], attempts=[WorkAttemptResult(
                number=1, status="failed", error=error, reception_error=error, warnings=[warning],
            )],
        )],
    )
    req = request([], parent_session_id="session_test", work_results=[work])
    before = req.model_dump_json()
    section = artifact_index_context(req, state())
    assert "BODY_ONLY_IN_TOOL_HISTORY" not in section.content
    assert "file:///private/result" not in section.content
    assert '"details"' not in section.content
    assert "Tool execution failed" in section.content and "Some evidence is missing" in section.content
    assert req.model_dump_json() == before
