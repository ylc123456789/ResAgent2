"""Legacy-module adapters.

These wrap the OLD ExpAgent module behind the ``ModulePort`` boundary. They are
transitional: the Scientific adapter is deleted when the native Scientific Agent
lands (Phase 7) and must not grow business logic. The experiment adapter was
already deleted in Phase 6 (native Experiment Agent replaces it).
"""

from .legacy_scientific import LegacyScientificAnalyzeAdapter

__all__ = [
    "LegacyScientificAnalyzeAdapter",
]
