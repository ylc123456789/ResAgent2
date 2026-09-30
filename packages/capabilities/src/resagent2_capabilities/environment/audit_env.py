"""Audit environment tool."""

from __future__ import annotations

from pydantic import BaseModel

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import RuntimeModel
from resagent2_components.environment import EnvironmentBinding

class AuditEnvInput(RuntimeModel):
    """Empty request that audits the bound base environment."""

    pass


class AuditEnvTool:
    """Check bound Python identity/version and pip availability, and report environment facts."""

    name = "audit_env"
    input_model = AuditEnvInput
    model_guidance = (
        "Use for diagnostics when current environment facts are missing or suspect. "
        "run_verification and run_command audit automatically when needed. A passing "
        "audit certifies only Python identity/version and pip availability. Package "
        "and device observations do not prove GPU initialization or task capability; "
        "verify needed capabilities through authorized execution tools."
    )

    def __init__(self, binding: EnvironmentBinding, *, allowed: bool = True) -> None:
        self.binding = binding
        self.allowed = allowed

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        if not self.allowed:
            raise PermissionError("Environment audit execution is not authorized")
        if self.binding.current is None:
            return ToolObservation(
                summary="No environment prepared; call prepare_environment first",
                ok=False,
                value={"blocked": True, "reason": "no_environment"},
            )
        audit = self.binding.audit()
        return ToolObservation(
            summary=(
                "Base environment audit passed (Python identity/version and pip only)"
                if audit.get("success")
                else "Environment audit failed: sys.prefix, pip or Python version "
                "does not match the bound env"
            ),
            value=audit,
            ok=bool(audit.get("success")),
            memory_updates={"env_audit": audit},
        )
