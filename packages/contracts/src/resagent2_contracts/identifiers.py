"""Stable Session names shared by callers and native Agents.

The SessionId body allows 128 characters. Hash complete identities rather than
truncating identifying suffixes; readable prefixes are only for diagnostics.
"""

import hashlib

from .models import RunId, SessionId, TaskId


def task_session_id(run_id: RunId, task_id: TaskId, attempt_number: int) -> SessionId:
    """Name one task attempt in a shared store, independently of ID length."""
    identity = f"{run_id}\0{task_id}\0{attempt_number}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    readable = f"{run_id}_{task_id}_{attempt_number}"
    return f"session_{readable[:95]}_{digest}"


def scientific_session_id(run_id: RunId) -> SessionId:
    """Give the controller and Scientific Agent the same bounded Run identity."""
    readable = f"scientific_{run_id}"
    if len(readable) <= 128:
        return f"session_{readable}"
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
    return f"session_{readable[:95]}_{digest}"
