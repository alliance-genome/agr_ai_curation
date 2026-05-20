"""
Search helper functions for AGR curation entity searches.

Provides shared enrichment logic for gene, allele, and other entity searches.
"""

from typing import Dict, Any


def enrich_with_match_context(
    result: Dict[str, Any],
    matched_entity: str,
    primary_symbol: str,
    entity_type: str
) -> Dict[str, Any]:
    """
    Add matched_on field if search matched a synonym rather than primary symbol.

    This helps the LLM understand HOW a result was found, especially when
    the search term was a synonym or alternative name.

    Args:
        result: The result dictionary to enrich
        matched_entity: What the search actually matched (from search results)
        primary_symbol: The official/primary symbol (from entity details)
        entity_type: 'gene', 'allele', etc.

    Returns:
        The result dict, potentially with matched_on and note fields added
    """
    if matched_entity and primary_symbol and matched_entity != primary_symbol:
        result["matched_on"] = matched_entity
        result["note"] = (
            f"Search matched synonym '{matched_entity}', "
            f"official {entity_type} symbol is '{primary_symbol}'"
        )

    return result
