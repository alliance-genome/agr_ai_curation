"""API module for Weaviate Control Panel."""

from . import documents
from . import chunks
from . import processing
from . import strategies
from . import settings
from . import schema
from . import health
from . import pdf_viewer
from . import feedback
from . import maintenance

__all__ = [
    "documents",
    "chunks",
    "processing",
    "strategies",
    "settings",
    "schema",
    "health",
    "pdf_viewer",
    "feedback",
    "maintenance",
]
