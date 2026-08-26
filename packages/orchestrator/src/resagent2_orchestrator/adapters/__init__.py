"""Legacy-module adapters.

These wrap the OLD CodingAgent / reproagent / ExpAgent modules behind the
``ModulePort`` boundary. They are transitional: each one is deleted when the
corresponding native Agent lands (Phase 5/6/7) and must not grow business logic.
"""

from .legacy_coding import LegacyCodingAdapter
from .legacy_experiment import LegacyExperimentAdapter
from .legacy_scientific import LegacyScientificAnalyzeAdapter

__all__ = [
    "LegacyCodingAdapter",
    "LegacyExperimentAdapter",
    "LegacyScientificAnalyzeAdapter",
]
