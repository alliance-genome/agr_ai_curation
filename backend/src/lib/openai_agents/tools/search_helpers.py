"""
Search helper functions for AGR curation entity searches.

Provides shared validation and enrichment logic for gene, allele,
and other entity searches. These helpers ensure consistent behavior
across all search methods.
"""

import logging
import re
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of symbol validation."""
    is_valid: bool
    warning_message: Optional[str] = None


def validate_search_symbol(symbol: str, entity_type: str) -> ValidationResult:
    """
    Validate a symbol before database search.

    Applies to: gene, allele (and future entity types)

    Checks for patterns that suggest genotype notation or other
    invalid patterns that won't exist in the database.

    Args:
        symbol: The symbol to validate
        entity_type: 'gene', 'allele', etc.

    Returns:
        ValidationResult with is_valid flag and optional warning message.
        If is_valid=False, the warning explains the issue but does NOT
        suggest a fix - the LLM should reason about the correct symbol.
    """
    # Handle None or whitespace-only symbols
    symbol = (symbol or "").strip()
    if not symbol:
        return ValidationResult(
            is_valid=False,
            warning_message=f"Empty {entity_type} symbol provided. Please provide a valid symbol."
        )

    # Check for whitespace (suggests genotype notation like "PIMT fl/fl")
    if re.search(r'\s', symbol):
        return ValidationResult(
            is_valid=False,
            warning_message=(
                f"Symbol '{symbol}' contains whitespace which suggests genotype notation "
                f"(e.g., fl/fl, +/+, -/-). These patterns are not stored in the database. "
                f"Consider extracting the base symbol and retrying, or set force=True with "
                f"force_reason if this is intentional."
            )
        )

    # Check for genotype-like slash patterns OUTSIDE parentheses
    # Allow: Tg(Vil1-cre/ERT2) - slash inside parens is OK
    # Flag: PIMT+/-, Gene+/+, Gene-/-, fl/fl, flox/flox
    without_parens = re.sub(r'\([^)]*\)', '', symbol)

    # Pattern: slash followed by common genotype indicators
    if re.search(r'/(fl|flox|\+|-)', without_parens, re.IGNORECASE):
        return ValidationResult(
            is_valid=False,
            warning_message=(
                f"Symbol '{symbol}' contains what appears to be genotype notation "
                f"(e.g., /fl, /+, /-). These zygosity indicators are not stored in the database. "
                f"Consider extracting the base symbol and retrying, or set force=True with "
                f"force_reason if this is intentional."
            )
        )

    # Pattern: fl/fl, flox/flox, +/+, +/-, -/- at the end (standalone zygosity)
    if re.search(r'(fl/fl|flox/flox|\+/\+|\+/-|-/-)$', without_parens, re.IGNORECASE):
        return ValidationResult(
            is_valid=False,
            warning_message=(
                f"Symbol '{symbol}' ends with zygosity notation (fl/fl, +/+, -/-, etc.) "
                f"which is not stored in the database. "
                f"Consider extracting the base symbol and retrying, or set force=True with "
                f"force_reason if this is intentional."
            )
        )

    return ValidationResult(is_valid=True)


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


def check_force_parameters(force: bool, force_reason: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate force/force_reason parameter combination.

    Args:
        force: Whether to skip validation
        force_reason: Explanation for skipping (required if force=True)

    Returns:
        Tuple of (is_valid, error_message)
        - If is_valid=True, proceed with forced search
        - If is_valid=False, return error_message to user
    """
    if force and not force_reason:
        return False, "force=True requires force_reason explaining why validation should be skipped"

    return True, None


def log_validation_override(symbol: str, entity_type: str, force_reason: str) -> None:
    """
    Log when validation is overridden with force=True.

    This creates a record in the logs (and Langfuse traces) that can be
    reviewed later to identify patterns in validation overrides.

    Args:
        symbol: The symbol that was searched despite validation warning
        entity_type: 'gene', 'allele', etc.
        force_reason: The reason provided for the override
    """
    logger.info(
        f"[search_helpers] Validation override: "
        f"entity_type='{entity_type}', symbol='{symbol}', reason='{force_reason}'"
    )
