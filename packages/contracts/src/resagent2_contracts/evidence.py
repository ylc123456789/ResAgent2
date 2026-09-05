"""Pure evidence requirements shared by the Agent and orchestration boundary."""

from collections.abc import Iterable

from .models import ArtifactRef, RunId


def missing_required_evidence_kinds(
    required_kinds: Iterable[str],
    *,
    run_id: RunId,
    artifacts: Iterable[ArtifactRef],
    observed_artifact_ids: Iterable[str],
    cited_artifact_ids: Iterable[str],
) -> list[str]:
    """Find required kinds not backed by registered, observed, cited evidence.

    Callers supply trusted ArtifactRefs, not model-reported metadata. Imported
    evidence and newly produced evidence follow the same rule; a requirement
    names an artifact kind, not the tool that must run to produce it.
    """
    used = set(observed_artifact_ids) & set(cited_artifact_ids)
    present = {
        artifact.kind
        for artifact in artifacts
        if artifact.run_id == run_id and artifact.id in used
    }
    return sorted(set(required_kinds) - present)
