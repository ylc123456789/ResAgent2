"""Bibliographic search operations; no model tool entry points."""

from ._http import (
    LiteratureSearchError,
    LiteratureUnavailableError,
)
from .backends import (
    ArxivLiteratureBackend,
    OpenAlexLiteratureBackend,
    MultiSourceLiteratureBackend,
    LiteraturePaper,
    LiteratureSearchBackend,
)

from .records import render_paper
