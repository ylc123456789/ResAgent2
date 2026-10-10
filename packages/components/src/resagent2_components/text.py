"""Shared UTF-8 text limits, bounded workspace reads, and window projection."""

from pathlib import Path


# Workspace processing bound, separate from tool output and model input.
MAX_WORKSPACE_TEXT_BYTES = 10 * 1024 * 1024


# Raw tool-result IO bound, not the model input budget. Context projections
# pack these records against each Agent's effective input limit.
MAX_READ_CHARS = 128_000


class TextFileTooLargeError(ValueError):
    """A workspace text file exceeds the configured processing bound."""


def decode_text(content: bytes) -> str:
    """Read UTF-8 without lossy substitution or NUL-bearing binary content."""
    if b"\x00" in content:
        raise ValueError(
            "Cannot read as UTF-8 text: file contains NUL bytes. "
            "Use a format-aware reader or authorized processing to inspect the file."
        )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(
            "Cannot read as UTF-8 text: file contains invalid UTF-8 bytes. "
            "Use a format-aware reader or authorized processing to inspect the file."
        ) from None


def read_text_file(
    path: Path, *, max_bytes: int = MAX_WORKSPACE_TEXT_BYTES,
) -> str:
    """Validate one bounded workspace text file while preserving its newlines."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if path.stat().st_size > max_bytes:
        raise TextFileTooLargeError(f"file is too large to process as text: limit {max_bytes} bytes")
    with path.open("rb") as handle:
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise TextFileTooLargeError(f"file is too large to process as text: limit {max_bytes} bytes")
    return decode_text(content)


def encode_text(
    text: str, *, max_bytes: int = MAX_WORKSPACE_TEXT_BYTES,
) -> bytes:
    """Validate UTF-8 workspace output before touching the destination."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if "\x00" in text:
        raise ValueError("Cannot write as UTF-8 text: content contains NUL bytes.")
    try:
        content = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Cannot write as UTF-8 text: content contains invalid Unicode characters.") from None
    if len(content) > max_bytes:
        raise TextFileTooLargeError(f"file is too large to write as text: limit {max_bytes} bytes")
    return content


def wrap_text_lines(text: str, *, max_chars: int = 1_000) -> str:
    """Insert line breaks without dropping source characters or whitespace."""
    return "\n".join(
        "\n".join(line[start:start + max_chars] for start in range(0, len(line), max_chars))
        for line in text.split("\n")
    )


def validate_text_window(
    *, start_line: int | None = None, end_line: int | None = None,
    start_char: int = 0, end_char: int | None = None,
) -> None:
    """Check one-based inclusive lines and zero-based exclusive characters."""
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be greater than or equal to 1")
    if end_line is not None and end_line < 1:
        raise ValueError("end_line must be greater than or equal to 1")
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ValueError("end_line must be greater than or equal to start_line")
    if start_char < 0 or (end_char is not None and end_char <= start_char):
        raise ValueError("character range must satisfy 0 <= start_char < end_char")


def slice_text_lines(
    text: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    start_char: int = 0,
    end_char: int | None = None,
    max_chars: int = MAX_READ_CHARS,
) -> dict:
    """Select physical lines, then a character window, then bound tool output.

    Request coordinates remain unchanged. ``next_start_char`` identifies the
    next source character in the same line selection, regardless of truncation.
    ``total_lines`` counts all physical lines in the source and
    ``selected_chars`` counts characters in the selected line range before
    applying the character window.
    """
    validate_text_window(
        start_line=start_line, end_line=end_line,
        start_char=start_char, end_char=end_char,
    )
    lines = text.splitlines(keepends=True)
    selected_lines = "".join(lines[(start_line or 1) - 1 : end_line])
    selected = selected_lines[start_char:end_char]
    content = selected[:max_chars]
    returned_end = start_char + len(content)
    return {
        "start_line": start_line,
        "end_line": end_line,
        "start_char": start_char,
        "end_char": end_char,
        "total_lines": len(lines),
        "selected_chars": len(selected_lines),
        "content": content,
        "truncated": len(selected) > max_chars,
        "next_start_char": returned_end if returned_end < len(selected_lines) else None,
    }
