"""
AGR Curation Database Client Helper

Provides production-ready access to the AGR Curation database using the
agr-curation-api-client package with direct database access mode.

All credential resolution is delegated to CurationConnectionResolver.
Do not read CURATION_DB_URL directly — use the resolver.

Usage in agents:
    from src.lib.database.agr_client import get_agr_db_client

    # Get database client (returns None if not configured)
    db = get_agr_db_client()
    if db:
        genes = db.get_genes_by_taxon("<taxon_curie>", limit=10)
"""

import logging
from typing import Optional

from src.lib.database.curation_resolver import get_curation_resolver

logger = logging.getLogger(__name__)


def get_agr_db_client(force_new: bool = False):
    """
    Get DatabaseMethods instance for AGR Curation database.

    Delegates all credential resolution to CurationConnectionResolver.

    Args:
        force_new: Force creation of new instance (resets resolver)

    Returns:
        DatabaseMethods instance ready for queries, or None if not configured

    Examples:
        db = get_agr_db_client()
        if db:
            genes = db.get_genes_by_taxon("<taxon_curie>", limit=10)
    """
    resolver = get_curation_resolver()

    if force_new:
        resolver.reset()

    return resolver.get_db_client()


def close_agr_db_client():
    """
    Close the singleton database client connection.

    Call this during application shutdown to properly close database connections.
    """
    resolver = get_curation_resolver()
    resolver.close()


# Convenience functions for common queries
def _require_agr_db_client():
    """Get configured client or raise clear runtime error."""
    db = get_agr_db_client()
    if db is None:
        raise RuntimeError(
            "AGR curation DB is not configured. "
            "Set CURATION_DB_URL or configure services.curation_db in config/connections.yaml."
        )
    return db


def get_genes_for_species(taxon_id: str, limit: Optional[int] = None) -> list:
    """
    Convenience function to get genes for a species.

    Args:
        taxon_id: NCBI Taxon ID (e.g., 'NCBITaxon:10090')
        limit: Maximum number of genes to return

    Returns:
        List of Gene objects
    """
    db = _require_agr_db_client()
    return db.get_genes_by_taxon(taxon_id, limit=limit)


def get_disease_annotations_for_species(taxon_id: str) -> list:
    """
    Convenience function to get disease annotations for a species.

    Args:
        taxon_id: NCBI Taxon ID (e.g., 'NCBITaxon:10090')

    Returns:
        List of dictionaries with disease annotation data
    """
    db = _require_agr_db_client()
    return db.get_disease_annotations(taxon_id)


def get_expression_annotations_for_species(taxon_id: str) -> list:
    """
    Convenience function to get expression annotations for a species.

    Args:
        taxon_id: NCBI Taxon ID (e.g., 'NCBITaxon:10090')

    Returns:
        List of dictionaries with expression annotation data
    """
    db = _require_agr_db_client()
    return db.get_expression_annotations(taxon_id)


def get_available_species() -> list:
    """
    Convenience function to get list of available species.

    Returns:
        List of tuples: (species_abbreviation, taxon_id)
        Example: [('WB', 'NCBITaxon:XXXXX'), ('FB', 'NCBITaxon:YYYYY'), ...]
    """
    db = _require_agr_db_client()
    return db.get_data_providers()
