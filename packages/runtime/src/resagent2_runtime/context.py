"""Deterministic context composition with one total budget."""

from __future__ import annotations

from math import ceil

from .models import ComposedContext, ContextSection


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
