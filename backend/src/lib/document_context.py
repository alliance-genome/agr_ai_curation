"""
Document context provider for agent creation.

Consolidates document metadata fetching (hierarchy, sections) with
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
    - sections: Flat list of section names (derived from hierarchy)
    - document_name: Optional filename for context in prompts

    Use DocumentContext.fetch() to create instances with automatic caching.
    """

    document_id: str
    user_id: str
    document_name: Optional[str] = None
    hierarchy: Optional[Dict[str, Any]] = None
    sections: Optional[List[str]] = field(default=None)

    @classmethod
    async def fetch(
        cls,
        document_id: str,
        user_id: str,
        document_name: Optional[str] = None,
    ) -> "DocumentContext":
        """Fetch document context with caching.

        Resolves the run's readable files first; the hierarchy lists only their
        pieces. Checks the cache first, then fetches from Weaviate if needed. A
        cache entry is used only for the same (user_id, document_id) and the same
        readable files, within its TTL.

        Args:
            document_id: UUID of the PDF document
            user_id: Cognito subject ID for tenant isolation
            document_name: Optional filename for prompt context

        Returns:
            DocumentContext with hierarchy and sections populated
        """
        from .document_files.readable import resolve_readable_files

        hierarchy = None
        sections = None
        # The outline lists the run's readable files only. Like the outline itself,
        # it is optional context: a failure is reported and the run goes on
        # without an outline (each tool call resolves its own readable files).
        readable = None
        try:
            readable = await asyncio.to_thread(resolve_readable_files, document_id, user_id)
        except Exception:
            # CancelledError is deliberately not caught.
            _report_preparation_failure("readable_files")

        if readable is not None:
            hierarchy = await cls._hierarchy_for(document_id, user_id, readable)
        if hierarchy is not None:
            # Files being added change without changing the readable files, so this
            # count is taken for every run and never cached with the outline.
            hierarchy = {**hierarchy, "files_being_added": readable.files_being_added}

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
            sections=sections,
        )

    @staticmethod
    async def _hierarchy_for(document_id: str, user_id: str, readable: Any) -> Optional[Dict[str, Any]]:
        """The outline of the readable files, from the cache or from Weaviate."""
        from .document_cache import get_cached_metadata, metadata_cache_fill
        from .weaviate_client.chunks import get_document_sections_hierarchical
        from .openai_agents.config import get_document_context_stage_timeout_seconds

        cached = get_cached_metadata(user_id, document_id, readable.fingerprint)
        if cached:
            hierarchy = cached.hierarchy
            logger.info(
                f"[DocumentContext] Cache hit for document {document_id[:8]}... "
                f"({len(hierarchy.get('sections', []) if hierarchy else [])} sections)"
            )
            return hierarchy

        logger.info('[DocumentContext] Cache miss, fetching from Weaviate: %s...', document_id[:8])
        hierarchy = None
        # Independent fills keep each request's contextvars and LLM costs local.
        # The lease is revoked by invalidation and released on cancellation.
        with metadata_cache_fill(user_id, document_id, readable.fingerprint) as publish:
            try:
                async with asyncio.timeout(get_document_context_stage_timeout_seconds()):
                    hierarchy = await get_document_sections_hierarchical(
                        document_id, user_id, readable=readable,
                    )
            except Exception:
                # CancelledError is deliberately not caught.
                _report_preparation_failure("hierarchy")
            publish(hierarchy)
        return hierarchy

    def to_agent_kwargs(self) -> Dict[str, Any]:
        """Convert to kwargs dict for unified agent builder calls.

        Returns a dictionary suitable for passing to get_agent_by_id(...)
        for document-aware agents.

        Returns:
            Dict with document_id, user_id, document_name, hierarchy
            and sections keys.
        """
        return {
            "document_id": self.document_id,
            "user_id": self.user_id,
            "document_name": self.document_name,
            "hierarchy": self.hierarchy,
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
