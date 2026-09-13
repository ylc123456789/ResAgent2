"""Small, shared text-window projection; no filesystem access or state."""


# Raw tool-result IO bound, not the model input budget. Context projections
# pack these records against each Agent's effective input limit.
MAX_READ_CHARS = 128_000


def wrap_text_lines(text: str, *, max_chars: int = 1_000) -> str:
    """Insert line breaks without dropping source characters or whitespace."""
    return "\n".join(
        "\n".join(line[start:start + max_chars] for start in range(0, len(line), max_chars))
        for line in text.split("\n")
    )


def slice_text_lines(
    text: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = MAX_READ_CHARS,
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
