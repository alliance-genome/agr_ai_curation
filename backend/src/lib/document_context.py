"""
Document context provider for agent creation.

Consolidates document metadata fetching (hierarchy, abstract, sections) with
TTL caching. Used by both chat flow (runner.py) and flow executor (executor.py)
to provide consistent document context to agents.

Key benefits:
- Single source of truth for document metadata
- Automatic caching to avoid redundant Weaviate queries
- Consistent context for agents regardless of entry point

Usage:
    from src.lib.document_context import DocumentContext

    # Fetch with caching (single Weaviate query per document)
    doc_ctx = DocumentContext.fetch(document_id, user_id, document_name)

    # Pass to agent factories
    agent = create_pdf_agent(**doc_ctx.to_agent_kwargs())
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DocumentContext:
    """Complete document context for agent creation.

    Encapsulates all document metadata needed by PDF-aware agents:
    - hierarchy: Hierarchical document structure from Weaviate
    - abstract: Paper abstract (extracted via multi-strategy search)
    - sections: Flat list of section names (derived from hierarchy)
    - document_name: Optional filename for context in prompts

    Use DocumentContext.fetch() to create instances with automatic caching.
    """

    document_id: str
    user_id: str
    document_name: Optional[str] = None
    hierarchy: Optional[Dict[str, Any]] = None
    abstract: Optional[str] = None
    sections: Optional[List[str]] = field(default=None)

    @classmethod
    def fetch(
        cls,
        document_id: str,
        user_id: str,
        document_name: Optional[str] = None,
    ) -> "DocumentContext":
        """Fetch document context with caching.

        Checks the cache first, then fetches from Weaviate if needed.
        The cache is keyed by (user_id, document_id) with 10-minute TTL.

        Args:
            document_id: UUID of the PDF document
            user_id: Cognito subject ID for tenant isolation
            document_name: Optional filename for prompt context

        Returns:
            DocumentContext with hierarchy, abstract, and sections populated
        """
        from .document_cache import get_cached_metadata, set_cached_metadata
        from .openai_agents.agents.supervisor_agent import fetch_document_hierarchy_sync
        from .openai_agents.prompt_utils import fetch_document_abstract_sync

        hierarchy = None
        abstract = None
        sections = None

        # Check cache first
        cached = get_cached_metadata(user_id, document_id)
        if cached:
            hierarchy = cached.hierarchy
            abstract = cached.abstract
            logger.info(
                f"[DocumentContext] Cache hit for document {document_id[:8]}... "
                f"({len(hierarchy.get('sections', []) if hierarchy else [])} sections)"
            )
        else:
            # Cache miss - fetch from Weaviate
            logger.info('[DocumentContext] Cache miss, fetching from Weaviate: %s...', document_id[:8])

            hierarchy = fetch_document_hierarchy_sync(document_id, user_id)
            if hierarchy:
                logger.info(
                    f"[DocumentContext] Fetched hierarchy: "
                    f"{len(hierarchy.get('sections', []))} sections"
                )

            # Fetch abstract (uses hierarchy's abstract_section_title if available)
            abstract = fetch_document_abstract_sync(document_id, user_id, hierarchy)
            if abstract:
                logger.info('[DocumentContext] Fetched abstract: %s chars', len(abstract))

            # Store in cache for subsequent requests
            set_cached_metadata(user_id, document_id, hierarchy, abstract)

        # Extract flat section names from hierarchy
        if hierarchy and hierarchy.get("sections"):
            sections = [
                s.get("name")
                for s in hierarchy.get("sections", [])
                if s.get("name")
            ]
            logger.debug('[DocumentContext] Extracted %s section names', len(sections))

        return cls(
            document_id=document_id,
            user_id=user_id,
            document_name=document_name,
            hierarchy=hierarchy,
            abstract=abstract,
            sections=sections,
        )

    def to_agent_kwargs(self) -> Dict[str, Any]:
        """Convert to kwargs dict for agent factory functions.

        Returns a dictionary suitable for passing to agent factories like
        create_pdf_agent() or create_gene_expression_agent().

        Returns:
            Dict with document_id, user_id, document_name, hierarchy,
            abstract, and sections keys.
        """
        return {
            "document_id": self.document_id,
            "user_id": self.user_id,
            "document_name": self.document_name,
            "hierarchy": self.hierarchy,
            "abstract": self.abstract,
            "sections": self.sections,
        }

    def has_structure(self) -> bool:
        """Check if document structure information is available.

        Returns:
            True if hierarchy or sections are populated.
        """
        return bool(self.hierarchy) or bool(self.sections)

    def section_count(self) -> int:
        """Get the number of sections in the document.

        Returns:
            Number of sections, or 0 if no structure available.
        """
        if self.sections:
            return len(self.sections)
        if self.hierarchy and self.hierarchy.get("sections"):
            return len(self.hierarchy.get("sections", []))
        return 0
