"""Run grants and deterministic operation rules share one tool policy."""

from resagent2_runtime import AllowListPermissionPolicy, PermissionDecision


class OperationPermissionPolicy(AllowListPermissionPolicy):
    """Prepare a bounded operation without performing or approving its effects."""

    def __init__(self, tools, *, boundary, request):
        super().__init__({tool.name for tool in tools})
        self.tools = {tool.name: tool for tool in tools}
        self.boundary = boundary
        self.answers = request.resume_answers

    def check(self, action, state, request):
        decision = super().check(action, state, request)
        if decision.outcome == "deny":
            return decision
        if action.tool == "run_shell" and not request.permissions.execute_commands:
            return PermissionDecision(outcome="deny", reason="Process execution is not authorized")
        if action.tool == "run_shell" and not self.boundary.grant.access.unrestricted:
            return PermissionDecision(outcome="deny", reason="Script execution requires a fully readable/writable trusted workspace; scoped execution needs an isolation backend")
        tool = self.tools[action.tool]
        arguments = tool.input_model.model_validate(action.arguments)
        context = {"cwd": str(self.boundary.root)}
        prepared = None
        if action.tool == "delete_path":
            prepared = tool.prepare(arguments)
            context["deletion"] = prepared
            if prepared["requires_confirmation"]:
                decision = PermissionDecision(outcome="ask", reason=f"Confirm deletion of {prepared['path']} ({len(prepared['entries'])} entries)")
        elif action.tool == "run_shell":
            context.update(tool.approval_context())
            prepared = dict(context)
            decision = PermissionDecision(outcome="ask", reason="Confirm this Bash script before execution")
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
                    or answer.task_id != request.task_id or answer.attempt_number != request.attempt_number
                    or answer.session_id != state.session_id):
                continue
            approved = answer.values.get("approve", "").strip().lower() in {"yes", "true", "approve", "approved", "确认", "同意", "是"}
            return decision.model_copy(update={"outcome": "allow" if approved else "deny",
                                               "reason": "Operation approved" if approved else "User declined this operation",
                                               "approval_id": pending.action_id,
                                               "user_comment": answer.comment})
        return decision
