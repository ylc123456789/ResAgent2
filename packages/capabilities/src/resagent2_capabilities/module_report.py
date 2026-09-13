"""Package selected module explanations for the existing artifact channel."""

from __future__ import annotations

from resagent2_contracts import ArtifactCandidate
from .text import wrap_text_lines


REPORT_LINE_CHARS = 1_000


def _wrap_lines(text: str) -> str:
    """Insert line breaks without dropping existing content or whitespace."""
    return wrap_text_lines(text, max_chars=REPORT_LINE_CHARS)


def build_module_report(details: dict[str, str | list[str]]) -> ArtifactCandidate:
    """Package caller-selected prose, never a full result or execution state.

    The caller selects the relevant answer, source paths or residual risks.
    This is a readable projection; the original payload keeps exact values.
    Bounded physical lines make long prose reachable through read_artifact's
    existing line ranges. Registration supplies identity and provenance.
    """
    sections = [
        "# Module report",
        "Module-provided explanation, not independently verified or measured "
        "evidence. Consult original evidence for factual claims.",
    ]
    for name, value in details.items():
        body = (
            _wrap_lines(value)
            if isinstance(value, str)
            else "\n".join("- " + _wrap_lines(item).replace("\n", "\n  ") for item in value)
        )
        sections.append(f"## {name}\n\n{body}")
    return ArtifactCandidate(
        kind="module_report",
        path="module_report.md",
        media_type="text/markdown",
        summary="Module-provided explanation and limitations, not measured evidence",
        content="\n\n".join(sections) + "\n",
    )
