"""Bounded command diagnostics projected from existing observations, without IO."""

from resagent2_runtime import AgentState, ContextSection


def _tail(text: object, limit: int) -> str:
    if not isinstance(text, str) or not text.strip():
        return "(no output captured)"[:limit]
    marker = "... [truncated]\n"
    if len(text) <= limit:
        return text
    if limit <= len(marker):
        return marker[:limit]
    return marker + text[-(limit - len(marker)):]


def command_context(state: AgentState, *, max_chars: int) -> ContextSection | None:
    """Keep each command tool's latest results; pack failures before successes.

    An old failure is historical diagnosis, not a current completion decision.
    The next result from the same tool supersedes it only in this projection;
    Session events and the complete log files are never edited or re-read.
    """
    records: list[tuple] = []
    seen: set[str] = set()
    for event in reversed(state.events):
        if event.type != "observation" or event.tool not in {
            "run_verification", "run_setup", "run_command",
        } or event.tool in seen:
            continue
        value = event.data.get("value")
        if not isinstance(value, dict):
            continue
        results = value.get("results", [value])
        if not isinstance(results, list):
            continue
        valid = [r for r in results if isinstance(r, dict) and isinstance(r.get("exit_code"), int)]
        if not valid:
            continue
        seen.add(event.tool)
        for index, result in enumerate(valid):
            failed = result["exit_code"] != 0 or result.get("timed_out", False)
            records.append((failed, event.sequence, index, event.tool, result))
    if not records:
        return None
    intro = (
        "Latest recorded command results per tool, not current workspace state. "
        "Failures are selected before successful output; observed_at is the original "
        "event sequence. Use these excerpts for execution diagnosis, not scientific "
        "measurements. Follow current control_state for verification validity.\n"
    )
    # Leave room for the omission count; metadata and tails share this envelope.
    if max_chars < len(intro) + 80:
        return ContextSection(
            name="command_results",
            content=f"Command results omitted: {len(records)}; diagnostic budget too small."[:max_chars],
            priority=96, required=True,
        )
    remaining = max_chars - len(intro) - 80
    selected = []
    for failed, sequence, index, tool, result in sorted(
        records, key=lambda r: (not r[0], -r[1], r[2]),
    ):
        command = str(result.get("command", ""))
        if len(command) > 400:
            command = command[:380] + " ... [truncated]"
        header = (
            f"\nobserved_at={sequence} {tool} result={index + 1} "
            f"{'failed' if failed else 'succeeded'}\n"
            f"command: {command}\nexit_code={result['exit_code']} "
            f"timed_out={bool(result.get('timed_out', False))}\n"
        )
        if remaining < len(header) + 80:
            continue
        body = header
        if failed:
            # Pick diagnostic fields BEFORE bounding; a successful command's
            # long paths/stdout cannot displace a failure in the middle of a batch.
            tail_limit = min(2_000, (remaining - len(header) - 40) // 2)
            body += "stdout_tail:\n" + _tail(result.get("stdout_tail"), tail_limit)
            body += "\nstderr_tail:\n" + _tail(result.get("stderr_tail"), tail_limit) + "\n"
        selected.append((sequence, index, body))
        remaining -= len(body)
    content = intro + "".join(item[2] for item in sorted(selected))
    content += f"\nOmitted command results: {len(records) - len(selected)}\n"
    return ContextSection(name="command_results", content=content, priority=96, required=True)
