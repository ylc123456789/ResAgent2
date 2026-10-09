"""Native protocol fixture builders for deterministic tests and mock E2E."""

import json
from uuid import uuid4

from pydantic import BaseModel

from resagent2_runtime import ToolCallTurn


def tool_turn(tool, arguments=None, *, call_id=None):
    return ToolCallTurn(tool_calls=[{
        "id": call_id or f"call_{uuid4().hex}", "name": tool,
        "arguments": json.dumps(arguments or {}),
    }])


def tool_turns(actions):
    actions = [action.model_dump(mode="json") if isinstance(action, BaseModel) else action
               for action in actions]
    return [tool_turn(action["tool"], action.get("arguments")) for action in actions]
