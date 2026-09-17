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
    """Prove the bound base environment is correct (framework-agnostic)."""

    name = "audit_env"
    input_model = AuditEnvInput

    def __init__(self, binding: EnvironmentBinding) -> None:
        self.binding = binding

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        if self.binding.current is None:
            return ToolObservation(
                summary="No environment prepared; call prepare_environment first",
                ok=False,
                value={"blocked": True, "reason": "no_environment"},
            )
        audit = self.binding.manager.audit(self.binding.current)
        self.binding.certified = bool(audit.get("success"))
        return ToolObservation(
            summary=(
                "Environment audit passed"
                if audit.get("success")
                else "Environment audit failed: sys.prefix, pip or Python version "
                "does not match the bound env"
            ),
            value=audit,
            ok=bool(audit.get("success")),
            memory_updates={"env_audit": audit},
        )
