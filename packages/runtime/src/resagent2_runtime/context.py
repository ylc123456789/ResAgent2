"""Deterministic context composition with one total budget."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import ceil, isfinite

from resagent2_contracts import RecordedAnswer

from .models import AgentState, ComposedContext, ContextSection


DEFAULT_AGENT_CONTEXT_TOKENS = 128_000
CONTEXT_TARGET_SHARE = 0.80


@dataclass(frozen=True, slots=True)
class ContextMaterial:
    """A render-on-demand section, never persisted in Run or Session.

    ``render(chars)`` must be pure and return bounded source material plus its
    provenance/omission markers. At zero it returns only the small navigation
    frame. ``weight`` is a relative starting share, not a local hard limit.
    The composer measures the complete request after every proposed expansion.
    """

    name: str
    render: Callable[[int], str]
    weight: float = 1
    priority: int = 0
    required: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip() or not isfinite(self.weight) or self.weight <= 0:
            raise ValueError("material name and a finite positive weight are required")


class ContextBudgetExceeded(ValueError):
    """Raised when required context alone cannot fit the configured budget."""


def user_answers_section(answers: Sequence[RecordedAnswer]) -> ContextSection | None:
    """Project caller-scoped question/reply pairs without caching or truncation.

    The caller selects the answers for this invocation, which may include
    earlier replies to the same task. The composer owns their total budget.
    """
    if not answers:
        return None
    return ContextSection(
        name="answers",
        content=(
            "User replies paired with their original question_text, in recorded order. "
            "These are answers, not tool results. An earlier ask_user [ok] only "
            "means a question was issued, not that its prerequisite was met. "
            "Use these replies with the current checked context; older tool "
            "observations may predate them.\n"
            + json.dumps([answer.model_dump(mode="json") for answer in answers], ensure_ascii=False)
        ),
        priority=80,
        required=True,
    )


class ContextComposer:
    """Reserve fixed context, then share one measured material allowance."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Return a deterministic character estimate, not a model tokenizer."""

        return 0 if not text else max(1, ceil(len(text) / 4))

    def compose(
        self,
        system_prompt: str,
        sections: list[ContextSection | ContextMaterial],
        *,
        max_tokens: int,
        measure: Callable[[str], int] | None = None,
    ) -> ComposedContext:
        """Pack sections using the complete rendered request's cost when supplied."""

        if max_tokens < 1:
            raise ContextBudgetExceeded("max_tokens must be positive")
        cost = measure or self.estimate_tokens

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
        materials: list[tuple[int, ContextMaterial]] = []
        omitted: list[str] = []
        text = ""
        for section in [*required, *optional]:
            material = section if isinstance(section, ContextMaterial) else None
            if material is not None:
                section = ContextSection(
                    name=material.name, content=material.render(0),
                    priority=material.priority, required=material.required,
                )
            rendered = f"## {section.name}\n{section.content}"
            candidate = f"{text}\n\n{rendered}" if included else rendered
            if cost(candidate) > max_tokens:
                if section.required:
                    raise ContextBudgetExceeded(
                        f"required context section {section.name!r} exceeds budget"
                    )
                omitted.append(section.name)
                continue
            included.append(section)
            if material is not None:
                materials.append((len(included) - 1, material))
            text = candidate

        # Share only the space below the same soft waterline that triggers
        # history compaction. Otherwise filling spare space could itself cause
        # an unnecessary summary on every turn. Required frames may exceed the
        # waterline, but never the caller's hard input limit.
        fill_limit = int(max_tokens * CONTEXT_TARGET_SHARE)
        spare = max(0, fill_limit - cost(text))
        weight = sum(material.weight for _, material in materials)
        char_limits: dict[int, int] = {}

        def grow(index: int, material: ContextMaterial, token_limit: int) -> None:
            nonlocal text
            used = cost(text)
            if token_limit <= used:
                return
            low = char_limits.get(index, 0)
            high = low + (token_limit - used) * 4
            best = included[index]
            best_limit = low
            while low <= high:
                trial = (low + high + 1) // 2
                section = included[index].model_copy(update={"content": material.render(trial)})
                candidate = "\n\n".join(
                    f"## {part.name}\n{part.content}"
                    for part in [*included[:index], section, *included[index + 1:]]
                )
                if cost(candidate) <= token_limit:
                    best, best_limit = section, trial
                    low = trial + 1
                else:
                    high = trial - 1
            included[index] = best
            char_limits[index] = best_limit
            text = "\n\n".join(f"## {part.name}\n{part.content}" for part in included)

        # First give every present material its starting share. Then let higher
        # priority materials borrow unused space, without evicting those shares.
        for index, material in materials:
            grow(index, material, min(fill_limit, cost(text) + int(spare * material.weight / weight)))
        for index, material in sorted(materials, key=lambda item: -item[1].priority):
            grow(index, material, fill_limit)
        return ComposedContext(
            text=text,
            included_sections=[section.name for section in included],
            omitted_sections=omitted,
            estimated_tokens=cost(text),
        )


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


def recent_tool_snippets(
    state: AgentState,
    *,
    tool: str | tuple[str, ...],
    identity_keys: tuple[str, ...],
    text_key: str,
    limit: int | None = None,
    max_total_chars: int,
) -> list[dict]:
    """Select recent unique snippets, then present them in observation order.

    One or several tools share this budget. Pack newest first until the budget
    is spent; only the final retained snippet is truncated and flagged (which
    can also be the newest snippet if it exceeds the whole budget). Anything
    older is dropped. Identity includes the tool and ``identity_keys`` (for
    read_file, ``("path", "start_line", "end_line")``),
    so two ranges of one file coexist instead of overwriting each other.
    ``observed_at`` preserves the source event sequence, not a file version.
    ``truncated`` describes the presented content; ``context_truncated`` marks
    extra clipping by this projection. The original events are never changed.
    """
    if (limit is not None and limit < 1) or max_total_chars < 1:
        raise ValueError("limit and max_total_chars must be positive")
    if not identity_keys:
        raise ValueError("identity_keys must not be empty")

    recent: list[dict] = []
    content_chars = 0
    seen: set[tuple] = set()
    tools = (tool,) if isinstance(tool, str) else tool
    for event in reversed(state.events):
        if event.type != "observation" or event.tool not in tools:
            continue
        data = event.data if isinstance(event.data, dict) else {}
        if not data.get("ok", True):
            continue
        value = data.get("value")
        if not isinstance(value, dict):
            continue
        content = value.get(text_key)
        if not isinstance(content, str):
            continue
        identity = (event.tool, *(value.get(key) for key in identity_keys))
        if identity in seen:
            continue
        seen.add(identity)
        recent.append({**value, "observed_at": event.sequence})
        content_chars += max(1, len(content))
        if content_chars >= max_total_chars or (limit is not None and len(recent) >= limit):
            break

    selected: list[dict] = []
    remaining = max_total_chars
    for value in recent:
        content = value[text_key]
        if len(content) <= remaining:
            selected.append(value)
            remaining -= len(content)
            continue
        if remaining == 0:
            break
        bounded = dict(value)
        bounded[text_key] = _head_tail(content, remaining)
        bounded["truncated"] = True
        bounded["context_truncated"] = True
        selected.append(bounded)
        break
    return list(reversed(selected))


def recent_tool_listing(
    state: AgentState,
    *,
    tool: str,
    list_key: str,
    max_entries: int = 80,
    max_chars: int = 2000,
) -> dict | None:
    """Return the latest bounded list observation for ``tool``, or None.

    Generic for list-shaped outputs (e.g. ``list_files``): keeps the most
    recent observation's ``list_key`` list, packed in order up to
    ``max_entries`` and ``max_chars`` without truncating an individual entry.
    The remaining value fields (e.g. the listed ``path``) are preserved, and
    any tool-level or budget-level truncation folds into one ``truncated``
    flag. Agents use it to retain "what files exist" without re-listing.
    """
    if max_entries < 1 or max_chars < 1:
        raise ValueError("max_entries and max_chars must be positive")
    for event in reversed(state.events):
        if event.type != "observation" or event.tool != tool:
            continue
        data = event.data if isinstance(event.data, dict) else {}
        value = data.get("value")
        if not isinstance(value, dict):
            continue
        entries = value.get(list_key)
        if not isinstance(entries, list):
            continue
        selected: list = []
        used = 0
        for entry in entries:
            if len(selected) >= max_entries:
                break
            cost = len(str(entry))
            if used + cost > max_chars:
                break
            selected.append(entry)
            used += cost
        bounded = dict(value)
        bounded["observed_at"] = event.sequence
        bounded[list_key] = selected
        bounded["truncated"] = bool(value.get("truncated")) or len(selected) < len(entries)
        return bounded
    return None
