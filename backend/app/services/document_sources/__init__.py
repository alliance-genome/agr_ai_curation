"""Document source adapters used by the unified pipeline."""

from .pdf_source import PDFDocumentSource
from .ontology_source import OntologyDocumentSource

__all__ = ["PDFDocumentSource", "OntologyDocumentSource"]
