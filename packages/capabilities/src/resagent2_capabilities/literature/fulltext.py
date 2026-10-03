"""On-demand full text for a registered paper; never arbitrary model URLs."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from resagent2_components.artifacts import ArtifactRegistrationPort, RegisteredArtifactReader
from resagent2_components.literature import LiteraturePaper
from resagent2_components.literature.fulltext import PdfFetchError, PdfParseError, fetch_pdf, parse_pdf
from resagent2_contracts import ArtifactCandidate, ArtifactId, ArtifactRef
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import RuntimeModel

from .literature_search import literature_outputs


class FetchLiteratureFulltextInput(RuntimeModel):
    paper_artifact_id: ArtifactId


class FetchLiteratureFulltextTool:
    """Acquire and freeze full text for one registered bibliographic source."""

    name = "fetch_literature_fulltext"
    input_model = FetchLiteratureFulltextInput
    model_guidance = (
        "Use an already imported PDF, or fetch the source-provided PDF of a registered "
        "literature_paper, and extract "
        "page-labelled text with OCR disabled. Input is the paper artifact ID, not "
        "a URL. The original PDF is preserved even if parsing fails. Repeated calls "
        "reuse registered originals/text. No PDF URL, network failure or unreadable "
        "pages are reported explicitly. Read the returned literature_fulltext with "
        "read_artifact as needed; obtaining a file does not mean its content was read. "
        "Extracted text may omit figures, formulas or table structure."
    )

    def __init__(self, register: ArtifactRegistrationPort, *,
                 input_artifacts: list[ArtifactRef] | None = None, download=None, parse=None):
        self.register = register
        self.input_artifacts = list(input_artifacts or [])
        self.download = download or fetch_pdf
        self.parse = parse or parse_pdf

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(FetchLiteratureFulltextInput, arguments)
        registered = list({ref.id: ref for ref in [
            *self.register.list_artifacts(run_id=state.run_id), *self.input_artifacts,
        ]}.values())
        reader = RegisteredArtifactReader(registered, run_id=state.run_id)
        paper_ref = reader.resolve_ref(args.paper_artifact_id)
        if paper_ref is None or paper_ref.kind != "literature_paper":
            raise ValueError("paper_artifact_id must name a registered paper in this Run")
        reader.verify(paper_ref.id)
        paper = LiteraturePaper.model_validate(paper_ref.metadata["paper"])
        refs = [ref for ref in registered
                if ref.metadata.get("paper_artifact_id") == paper_ref.id]
        pdf = next((ref for ref in reversed(refs) if ref.kind == "literature_pdf"), None)
        text = next((ref for ref in reversed(refs) if ref.kind == "literature_fulltext"), None)
        if text is not None:
            reader.verify(text.id)
            source = reader.resolve_ref(text.metadata.get("source_artifact_id", ""))
            if (source is None or source.kind != "literature_pdf"
                    or source.metadata.get("paper_artifact_id") != paper_ref.id
                    or source.metadata.get("source_artifact_id") != paper_ref.id):
                raise ValueError("full text has no registered source PDF for this paper")
            reader.verify(source.id)
            return self._result(state, paper_ref, source, text, cached=True)

        if pdf is not None and pdf.metadata.get("source_artifact_id") != paper_ref.id:
            raise ValueError("PDF has no registered source paper")

        if pdf is None:
            if not paper.pdf_url:
                return self._result(state, paper_ref, error="No source-provided PDF URL is available.", status="unavailable")
            token = hashlib.sha256(paper.key.encode()).hexdigest()[:16]
            with tempfile.TemporaryDirectory(prefix="resagent2-paper-") as temporary:
                path = Path(temporary) / "paper.pdf"
                try:
                    self.download(paper.pdf_url, path)
                except PdfFetchError as error:
                    return self._result(state, paper_ref, error=str(error), status="download_failed")
                # Freeze the original before extraction, so parse failure retains it.
                pdf = self.register.register_scientific(
                    ArtifactCandidate(
                        kind="literature_pdf", path=f"paper_{token}.pdf",
                        media_type="application/pdf", summary=f"{paper.title} — original PDF",
                        metadata={"paper_artifact_id": paper_ref.id,
                                  "source_artifact_id": paper_ref.id,
                                  "source_url": paper.pdf_url},
                    ), run_id=state.run_id, session_id=state.session_id,
                    content_bytes=path.read_bytes(),
                )
        reader = RegisteredArtifactReader([pdf], run_id=state.run_id)
        path = reader.verified_path(pdf.id)
        try:
            parsed = self.parse(path)
        except PdfParseError as error:
            return self._result(state, paper_ref, pdf, error=str(error), status="parse_failed")
        text = self.register.register_scientific(
            ArtifactCandidate(
                kind="literature_fulltext", path="paper_fulltext.md",
                media_type="text/markdown", summary=f"{paper.title} — extracted full text",
                metadata={
                    "paper_artifact_id": paper_ref.id, "source_artifact_id": pdf.id,
                    "parser": "pymupdf4llm", "parser_version": parsed.parser_version,
                    "page_count": parsed.page_count, "warnings": parsed.warnings,
                    "ocr": False,
                },
                content=f"Paper: {paper.title}\nOriginal PDF artifact: {pdf.id}\n\n"
                        "Extracted text; figures, formulas and table structure may be incomplete.\n\n"
                        + parsed.markdown,
            ), run_id=state.run_id, session_id=state.session_id,
        )
        return self._result(state, paper_ref, pdf, text)

    @staticmethod
    def _result(state, paper, pdf=None, text=None, *, cached=False, error=None, status="ready"):
        refs = [ref for ref in (pdf, text) if ref is not None]
        return ToolObservation(
            ok=error is None,
            summary=error or "Full text is available; use read_artifact for the needed pages.",
            value={
                "status": status, "paper_artifact_id": paper.id,
                "pdf_artifact_id": pdf.id if pdf else None,
                "fulltext_artifact_id": text.id if text else None,
                "cached": cached, "error": error,
                "warnings": text.metadata.get("warnings", []) if text else [],
            },
            memory_updates=literature_outputs(state, refs),
        )
