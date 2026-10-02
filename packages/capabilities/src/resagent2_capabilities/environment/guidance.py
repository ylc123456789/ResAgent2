"""Shared behavioral guidance for environment-aware model tools."""

ENVIRONMENT_DECISION_GUIDANCE = """The managed environment is shared across agents in the same Run and workspace.
Choose dependencies from project requirements and current hardware, driver and
framework build facts. A prior CPU command does not require CPU-only dependencies;
a framework/device failure does not prove that no GPU exists. Basic certification
checks bound Python and pip, not dependency compatibility or task/device readiness.
Prefer an available GPU for work that benefits from it, subject to task constraints.
If inherited dependencies cannot meet this task and need changing, diagnose first,
then ask_user before changing them or rebuilding the shared environment. Explain
the evidence, proposed change, affected checks and fallback costs. Await the answer
without changing the environment or silently accepting a reduced experiment.
Follow the user's decision for that change; do not ask for the same decision again.
Existing operation permissions and command confirmations still apply.
After authorized environment changes, revalidate affected capabilities and checks;
old successes describe the old environment."""

__all__ = ["ENVIRONMENT_DECISION_GUIDANCE"]
