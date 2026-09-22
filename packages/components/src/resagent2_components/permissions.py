"""Run grants and deterministic operation rules share one tool policy."""

from resagent2_contracts import RecordedAnswer
from .artifacts import RegisteredArtifactReader
from .materials import read_artifact_json
from .operations import command_decision
from resagent2_runtime import AllowListPermissionPolicy, PermissionDecision


_PROCESS_TOOLS = frozenset({"run_command", "run_setup", "run_verification", "audit_env"})
_ENVIRONMENT_TOOLS = frozenset({"prepare_environment", "run_setup"})


class OperationPermissionPolicy(AllowListPermissionPolicy):
    """Prepare a bounded operation without performing or approving its effects."""

    def __init__(self, tools, *, boundary, binding, request):
        super().__init__({tool.name for tool in tools})
        self.tools = {tool.name: tool for tool in tools}
        self.boundary = boundary
        self.binding = binding
        reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
        self.answers = [read_artifact_json(reader, ref.id, RecordedAnswer)
                        for ref in request.input_artifacts
                        if ref.kind == "answer" and ref.id in request.resume_artifact_ids]

    def check(self, action, state, request):
        decision = super().check(action, state, request)
        if decision.outcome == "deny":
            return decision
        process = action.tool in _PROCESS_TOOLS
        environment = action.tool in _ENVIRONMENT_TOOLS
        if process and not request.permissions.execute_commands:
            return PermissionDecision(outcome="deny", reason="Process execution is not authorized")
        if environment and not request.permissions.prepare_environment:
            return PermissionDecision(outcome="deny", reason="Environment preparation is not authorized")
        if action.tool in {"run_command", "run_setup", "run_verification"} and not self.boundary.grant.access.unrestricted:
            return PermissionDecision(outcome="deny", reason="Script execution requires a fully readable/writable trusted workspace; scoped execution needs an isolation backend")
        tool = self.tools[action.tool]
        arguments = tool.input_model.model_validate(action.arguments)
        if action.tool == "run_verification":
            workflow = tool.permission_policy.check(arguments.commands)
            if not workflow.allowed:
                return PermissionDecision(outcome="deny", reason=workflow.reason)
        if action.tool == "run_setup":
            workflow = tool.policy.check(arguments.command)
            if not workflow.allowed:
                return PermissionDecision(outcome="deny", reason=workflow.reason)
        context = {"cwd": str(self.boundary.root)}
        prepared = None
        if action.tool == "delete_path":
            prepared = tool.prepare(arguments)
            context["deletion"] = prepared
            if prepared["requires_confirmation"]:
                decision = PermissionDecision(outcome="ask", reason=f"Confirm deletion of {prepared['path']} ({len(prepared['entries'])} entries)")
        elif action.tool in {"run_command", "run_verification"}:
            commands = [arguments.command] if action.tool == "run_command" else arguments.commands
            for command in commands:
                current = command_decision(command, self.boundary)
                if current.outcome == "deny":
                    return current
                if current.outcome == "ask":
                    decision = current
        if process or environment:
            current = self.binding.current
            context["environment"] = str(current.prefix) if current is not None else None
            context["python_version"] = current.python_version if current is not None else self.binding.hard_constraint
            if request.confirm_commands:
                decision = PermissionDecision(outcome="ask", reason="This Run requires confirmation before external operations")
        if action.tool == "run_verification" and decision.outcome == "ask" and len(arguments.commands) != 1:
            return PermissionDecision(outcome="deny", reason="Submit one verification command at a time when confirmation is required")
        decision = decision.model_copy(update={"context": context, "prepared": prepared})
        if decision.outcome != "ask":
            return decision
        pending = state.pending_action
        if (pending is None or pending.run_id != request.run_id
                or pending.session_id != state.session_id
                or pending.task_id != request.task_id or pending.attempt_number != request.attempt_number
                or pending.tool != action.tool or pending.arguments != arguments.model_dump(mode="json")
                or pending.context != context):
            return decision
        for answer in self.answers:
            if (answer.action != pending or answer.run_id != request.run_id
                    or answer.task_id != request.task_id or answer.attempt_number != request.attempt_number):
                continue
            approved = answer.values.get("approve", "").strip().lower() in {"yes", "true", "approve", "approved", "确认", "同意", "是"}
            return decision.model_copy(update={"outcome": "allow" if approved else "deny",
                                               "reason": "Operation approved" if approved else "User declined this operation",
                                               "approval_id": pending.action_id})
        return decision
