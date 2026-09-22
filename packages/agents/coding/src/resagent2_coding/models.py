"""The single Coding action schema."""

from typing import Literal

from resagent2_runtime import AgentAction


class CodingAction(AgentAction):
    tool: Literal[
        "list_files", "read_file", "search_text", "read_artifact", "git_diff",
        "create_file", "replace_text", "delete_path", "prepare_environment", "run_setup",
        "audit_env", "run_verification", "finish", "ask_user",
    ]
