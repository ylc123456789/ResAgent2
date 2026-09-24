
from resagent2_contracts import WorkspaceAccess, RunPermissions, ExecutionLimits
import json
"""Exercise real analysis artifacts across native Agent and controller boundaries."""

import subprocess
import json

from resagent2_components import (
    RegisteredArtifactReader,
    ResourceLayout,
)
from resagent2_contracts import (
    AgentOwner, WorkflowAgentKind, WorkflowAgentDefinition, WorkflowAgentRegistry,
    ResearchRequest, RunBudget, RunStatus,
    FutureArtifactBinding, TaskProposal, WorkflowProposal, WorkspaceSourceKind, WorkspaceSpec,
)
from resagent2_coding import NativeCodingAgent
from resagent2_orchestrator import (
    DeterministicWorkInterpreter,
    CompilationResult, JsonRunStore, ModuleBinding, ResearchController,
    WorkflowScheduler,
)
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


def test_analysis_reaches_dependent_agent_and_scientific_through_frozen_artifact(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "util.py").write_text("VALUE = 42\n", encoding="utf-8")
    for args in [
        ["init", "-q"], ["add", "util.py"],
        ["-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "-qm", "baseline"],
    ]:
        subprocess.run(["git", *args], cwd=repo, check=True)

    answer = "The inspected constant is 42; ANALYSIS_HANDOFF_SENTINEL."
    uncertainty = "STATIC_ONLY_SENTINEL: runtime behavior was not tested."
    report_id = "artifact_inspect_1_1"
    read_file = {"tool": "read_file", "arguments": {"path": "util.py"}}

    def finish(text):
        return {"tool": "finish", "arguments": {
            "report": text,
            "artifacts": [{
                "kind": "module_report", "path": "module_report.md", "media_type": "text/markdown",
                "output_name": "analysis",
                "summary": "Code explanation", "content": (
                    f"## answer\n\n{text}\n\n## uncertainty\n\n{uncertainty}\n\n## evidence_files\n\nutil.py"
                ),
            }],
        }}

    clients = {
        "task_inspect": ScriptedLLMClient([read_file, finish(answer)]),
        "task_followup": ScriptedLLMClient([
            {"tool": "read_artifact", "arguments": {"artifact_id": report_id}},
            read_file, finish("Follow-up inspected the shared analysis and source."),
        ]),
        "task_independent": ScriptedLLMClient([read_file, finish("Independent inspection.")]),
    }
    requests = {}
    sessions = JsonSessionStore(tmp_path / "coding_sessions")
    layout = ResourceLayout(resource_root=tmp_path / "resources")

    class CodingPort:
        def invoke(self, request):
            requests[request.task_id] = request
            return NativeCodingAgent(
                clients[request.task_id], store=sessions, resource_layout=layout,
            ).invoke(request)

    class Compiler:
        def compile(self, request, **kwargs):
            assert kwargs["current"] is None
            return CompilationResult(WorkflowProposal(
                work_request_id=request.id,
                tasks=[TaskProposal(
                    id=task_id, work_request_id=request.id,
                    workflow_agent_kind=WorkflowAgentKind.CODING,
                    instruction="Inspect the code without changing it. Where is VALUE defined?",
                    input_artifact_bindings=[
                        FutureArtifactBinding(source_task=dependencies[0], output_selector="analysis"),
                    ] if dependencies else [],
                    output_names=["analysis"],
                    workspace_id="ws_main", depends_on=dependencies,
                ) for task_id, dependencies in [
                    ("task_inspect", []),
                    ("task_followup", ["task_inspect"]),
                    ("task_independent", []),
                ]],
            ))

    scientific_client = ScriptedLLMClient([
        {"tool": "request_work", "arguments": {
            "assessment": {"statement": "Need code inspection, not a measured experiment"},
            "work_request": {
                "objective": "Inspect the code and provide the analysis to a follow-up",
                "expected_evidence": ["Code analysis with its uncertainty"],
            },
        }},
        {"tool": "read_artifact", "arguments": {"artifact_id": report_id}},
        {"tool": "finish", "arguments": {"report": "Scientific conclusion", "artifacts": [{"kind": "scientific_opinion", "path": "opinion.json", "media_type": "application/json", "summary": "Scientific conclusion", "content": json.dumps({
                "verdict": "not_applicable", "statement": answer,
                "limitations": [uncertainty], "evidence_artifact_ids": [report_id],
            })}]}},
    ])
    scheduler = WorkflowScheduler(bindings={WorkflowAgentKind.CODING: ModuleBinding(owner=AgentOwner.CODING, port=CodingPort())}, store=JsonRunStore(tmp_path / 'runs'), artifact_root=tmp_path / 'artifacts', data_root=tmp_path / 'data', workspaces={'ws_main': WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(repo), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=ScientificAgent(
            scientific_client, store=JsonSessionStore(tmp_path / "scientific_sessions"),
            resource_layout=layout,
        ),
        compiler=Compiler(), scheduler=scheduler,
        registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(
            workflow_agent_kind=WorkflowAgentKind.CODING,
            description="Read-only code analysis",
        )]),
    )
    run = controller.create_run('run_handoffs', ResearchRequest(goal='Explain the code, with the analysis available to subsequent work', budget=RunBudget(max_llm_calls=30, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=3, max_attempts_per_task=1)))

    assert run.status == RunStatus.COMPLETED, run.model_dump(mode="json")
    assert all(task.status == "completed" for task in run.workflow.tasks)
    assert not any(a.kind == "module_report" for a in requests["task_inspect"].input_artifacts)
    assert not any(a.kind == "module_report" for a in requests["task_independent"].input_artifacts)
    assert [a.id for a in requests["task_followup"].input_artifacts if a.kind == "module_report"] == [report_id]
    # Check actual native-Agent prompts after read_artifact, not just stored payload.
    for context in [clients["task_followup"].contexts[1], scientific_client.contexts[-1]]:
        assert answer in context.text
        assert uncertainty in context.text
        assert "artifact_reads" in context.included_sections
    assert report_id in scientific_client.contexts[1].text
    assert '"kind": "module_report"' in scientific_client.contexts[1].text
    context_payloads = [json.loads(line) for line in scientific_client.contexts[1].text.splitlines()
                        if line.startswith("{")]
    materials = next(value for value in context_payloads if "index_artifact_id" in value and "index" in value)
    assert materials["index"]["run_id"] == run.run_id
    assert report_id in {entry["artifact_id"] for group in materials["index"]["groups"]
                         for entry in group["artifacts"]}
    assert '"index_changes"' not in scientific_client.contexts[1].text
    assert '"work_outcome"' not in scientific_client.contexts[1].text
    assert run.final_opinion.statement == answer
    assert run.final_opinion.limitations == [uncertainty]

    restored = scheduler.store.load(run.run_id)
    report = restored.artifacts[report_id]
    body = RegisteredArtifactReader([report], run_id=run.run_id).read_text(report_id)
    assert report.media_type == "text/markdown"
    assert f"## answer\n\n{answer}" in body["content"]
    assert f"## uncertainty\n\n{uncertainty}" in body["content"]
    assert "## evidence_files" in body["content"]
    assert "util.py" in body["content"]
    assert report_id in restored.scientific_observed_artifact_ids
    assert restored.llm_calls_used == 10  # 2 + 3 + 2 Coding calls, 3 Scientific calls.
    assert not (repo / "module_report.md").exists()
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=repo) == b""
