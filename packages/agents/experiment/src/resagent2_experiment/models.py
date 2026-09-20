"""The single Experiment action schema."""

from typing import Literal

from resagent2_runtime import AgentAction


class ExperimentAction(AgentAction):
    tool: Literal[
        "list_files", "read_file", "search_text", "read_artifact",
        "prepare_environment", "run_setup", "audit_env", "run_command",
        "finish", "ask_user",
    ]
