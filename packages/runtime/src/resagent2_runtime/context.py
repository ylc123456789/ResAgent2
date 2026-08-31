"""Deterministic context composition with one total budget."""

from __future__ import annotations

from math import ceil

from .models import AgentState, ComposedContext, ContextSection


class ContextBudgetExceeded(ValueError):
    """Raised when required context alone cannot fit the configured budget."""


class ContextComposer:
    """Includes required sections first, then optional sections by priority."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Return a deterministic conservative approximation for prompt budgeting."""

        return 0 if not text else max(1, ceil(len(text) / 4))

    def compose(
        self,
        system_prompt: str,
        sections: list[ContextSection],
        *,
        max_tokens: int,
    ) -> ComposedContext:
        """Compose a context without exceeding max_tokens."""

        if max_tokens < 1:
            raise ContextBudgetExceeded("max_tokens must be positive")

        system = ContextSection(
            name="system",
            content=system_prompt,
            priority=0,
            required=True,
        )
        required = [system, *(section for section in sections if section.required)]
        optional = sorted(
            (section for section in sections if not section.required),
            key=lambda section: -section.priority,
        )

        included: list[ContextSection] = []
        used = 0
        for section in required:
            cost = self.estimate_tokens(section.content)
            if used + cost > max_tokens:
                raise ContextBudgetExceeded(
                    f"required context section {section.name!r} exceeds budget"
                )
            included.append(section)
            used += cost

        omitted: list[str] = []
        for section in optional:
            cost = self.estimate_tokens(section.content)
            if used + cost <= max_tokens:
                included.append(section)
                used += cost
            else:
                omitted.append(section.name)

        text = "\n\n".join(
            f"## {section.name}\n{section.content}" for section in included
        )
        return ComposedContext(
            text=text,
            included_sections=[section.name for section in included],
            omitted_sections=omitted,
            estimated_tokens=used,
        )


def recent_tool_text_values(
    state: AgentState,
    *,
    tool: str,
    identity_key: str,
    text_key: str,
    limit: int = 4,
    max_total_chars: int = 6000,
) -> dict[str, str]:
    """Return recent unique tool text under one total context budget.

    Agent-owned context builders use this to retain domain observations such as
    file contents that the generic Runtime history intentionally abbreviates.
    The bound applies to the whole returned mapping, not independently to every
    item, so callers can safely make the resulting section required.
    """
    if limit < 1 or max_total_chars < 1:
        raise ValueError("limit and max_total_chars must be positive")

    recent: list[tuple[str, str]] = []
    seen: set[str] = set()
    for event in reversed(state.events):
        if event.type != "observation" or event.tool != tool:
            continue
        data = event.data if isinstance(event.data, dict) else {}
        value = data.get("value")
        if not isinstance(value, dict):
            continue
        identity = value.get(identity_key)
        content = value.get(text_key)
        if not isinstance(identity, str) or not isinstance(content, str):
            continue
        if identity in seen:
            continue
        recent.append((identity, content))
        seen.add(identity)
        if len(recent) >= limit:
            break

    if not recent:
        return {}
    share = max(1, max_total_chars // len(recent))
    return {identity: _head_tail(content, share) for identity, content in recent}


def _head_tail(text: str, max_chars: int) -> str:
    """Bound text while preserving both its beginning and end."""
    if len(text) <= max_chars:
        return text
    marker = "\n... [truncated] ...\n"
    if max_chars <= len(marker):
        return text[:max_chars]
    available = max_chars - len(marker)
    head = (available + 1) // 2
    tail = available // 2
    return text[:head] + marker + text[-tail:]
