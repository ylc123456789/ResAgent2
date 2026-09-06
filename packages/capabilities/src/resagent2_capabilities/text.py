"""Small, shared text-window projection; no filesystem access or state."""


def slice_text_lines(
    text: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = 8_000,
) -> dict:
    """Select inclusive, one-based lines, then bound their visible characters."""
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be greater than or equal to 1")
    if end_line is not None and end_line < 1:
        raise ValueError("end_line must be greater than or equal to 1")
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ValueError("end_line must be greater than or equal to start_line")
    lines = text.splitlines(keepends=True)
    selected = "".join(lines[(start_line or 1) - 1 : end_line])
    return {
        "start_line": start_line,
        "end_line": end_line,
        "content": selected[:max_chars],
        "truncated": len(selected) > max_chars,
    }
