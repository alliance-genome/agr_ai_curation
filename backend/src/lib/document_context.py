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

    # Await preparation with caching
    doc_ctx = await DocumentContext.fetch(document_id, user_id, document_name)

    # Pass to unified agent builder kwargs
    kwargs = doc_ctx.to_agent_kwargs()
"""

import asyncio
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
    async def fetch(
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
        from .document_cache import get_cached_metadata, metadata_cache_fill
        from .weaviate_client.chunks import get_document_sections_hierarchical
        from .openai_agents.prompt_utils import fetch_document_abstract
        from .openai_agents.config import get_document_context_stage_timeout_seconds

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

            # Independent fills keep each request's contextvars and LLM costs local.
            # The lease is revoked by invalidation and released on cancellation.
            with metadata_cache_fill(user_id, document_id) as publish:
                for stage in ("hierarchy", "abstract"):
                    try:
                        async with asyncio.timeout(get_document_context_stage_timeout_seconds()):
                            if stage == "hierarchy":
                                hierarchy = await get_document_sections_hierarchical(document_id, user_id)
                            else:
                                abstract = await fetch_document_abstract(document_id, user_id, hierarchy)
                    except Exception:
                        # CancelledError is deliberately not caught. A hierarchy
                        # failure permits abstract discovery without its metadata.
                        _report_preparation_failure(stage)
                publish(hierarchy, abstract)

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
        """Convert to kwargs dict for unified agent builder calls.

        Returns a dictionary suitable for passing to get_agent_by_id(...)
        for document-aware agents.

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


def _report_preparation_failure(stage: str) -> None:
    """Optional metadata/reporting failures must not fail agent execution."""
    from .observability.runtime import report_runtime_exception, sanitized_runtime_error

    try:
        report_runtime_exception(
            sanitized_runtime_error("Document context preparation failed"),
            component="document_context",
            operation="fetch_document_context_failed",
            tags={"phase": "context_preparation"},
            context={"stage": stage},
        )
    except Exception:
        pass
    logger.warning("Document context %s preparation failed", stage, extra={"sentry_skip_event": True})
