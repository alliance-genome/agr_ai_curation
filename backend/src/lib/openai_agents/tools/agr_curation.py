"""
AGR Curation Database tool for OpenAI Agents SDK.

Provides structured access to the Alliance Genome Resources Curation Database
using the official agr-curation-api-client package.
"""

import logging
import os
import re
from typing import Optional, List, Dict, Any, Tuple

from pydantic import BaseModel
from agents import function_tool

from src.lib.database.agr_client import get_agr_db_client
from src.lib.identifier_validation import is_valid_curie
from .search_helpers import (
    validate_search_symbol,
    enrich_with_match_context,
    check_force_parameters,
    log_validation_override,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = int(os.getenv("AGR_DEFAULT_LIMIT", "100"))
HARD_MAX = int(os.getenv("AGR_HARD_MAX", "500"))


class AgrQueryResult(BaseModel):
    status: str
    data: Any = None
    count: Optional[int] = None
    warnings: Optional[List[str]] = None
    message: Optional[str] = None


# Provider to taxon mapping
PROVIDER_TO_TAXON = {
    'WB': 'NCBITaxon:6239',      # C. elegans
    'FB': 'NCBITaxon:7227',      # D. melanogaster
    'MGI': 'NCBITaxon:10090',    # M. musculus
    'RGD': 'NCBITaxon:10116',    # R. norvegicus
    'ZFIN': 'NCBITaxon:7955',    # D. rerio
    'SGD': 'NCBITaxon:559292',   # S. cerevisiae
    'HGNC': 'NCBITaxon:9606',    # H. sapiens
}

# Reverse mapping: taxon to MOD abbreviation
TAXON_TO_PROVIDER = {v: k for k, v in PROVIDER_TO_TAXON.items()}

# MODs with useful creator/institution info in allele fullnames
MODS_WITH_FULLNAME_ATTRIBUTION = {'MGI', 'RGD'}


def _validate_curie_in_result(result: Dict[str, Any], curie_field: str = "curie") -> Dict[str, Any]:
    """Add CURIE validation metadata to a result dict."""
    curie = result.get(curie_field)
    if curie:
        result["curie_validated"] = is_valid_curie(curie)
        if not result["curie_validated"]:
            logger.warning('Invalid CURIE prefix detected: %s', curie)
    else:
        result["curie_validated"] = False
    return result


def _validate_curie_list(results: List[Dict[str, Any]], curie_field: str = "curie") -> Tuple[List[Dict[str, Any]], int]:
    """Validate CURIEs in a list of results."""
    invalid_count = 0
    for result in results:
        _validate_curie_in_result(result, curie_field)
        if not result.get("curie_validated", False):
            invalid_count += 1
    return results, invalid_count


def _normalize_allele_symbol_for_db(symbol: str) -> List[str]:
    """
    Normalize allele symbols for database search.

    The AGR database stores allele symbols with HTML superscript tags:
    - Database format: Arx<sup>tm1Gldn</sup>
    - Paper format: Arx<tm1Gldn>

    This function converts paper notation to database format.
    Returns list of search variants to try (original + normalized).
    """
    variants = [symbol]  # Always try original first

    # Convert angle brackets to HTML sup tags: Gene<allele> -> Gene<sup>allele</sup>
    angle_match = re.match(r'^([A-Za-z0-9]+)<([^>]+)>(.*)$', symbol)
    if angle_match:
        gene = angle_match.group(1)
        allele = angle_match.group(2)
        suffix = angle_match.group(3)
        # Add the database format with <sup> tags
        variants.append(f"{gene}<sup>{allele}</sup>{suffix}")
        # Also try without any brackets (concatenated)
        variants.append(f"{gene}{allele}{suffix}")

    return variants


def _normalize_limit(limit: Optional[int]) -> Tuple[int, List[str]]:
    """Normalize limit with defaults and caps."""
    warnings = []

    if limit is None:
        limit = DEFAULT_LIMIT
        warnings.append(f"default_limit_applied:{DEFAULT_LIMIT}")

    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        warnings.append(f"invalid_limit_defaulted:{DEFAULT_LIMIT}")
        limit_int = DEFAULT_LIMIT

    if limit_int <= 0:
        warnings.append(f"non_positive_limit_defaulted:{DEFAULT_LIMIT}")
        limit_int = DEFAULT_LIMIT

    if limit_int > HARD_MAX:
        warnings.append(f"limit_capped_at:{HARD_MAX}")
        limit_int = HARD_MAX

    return limit_int, warnings


def _extract_fullname_attribution(fullname: Optional[str], taxon_id: str) -> Optional[Dict[str, Any]]:
    """
    Extract probable creator/institution from allele fullname suffix.

    IMPORTANT: This is a HEURISTIC extraction based on naming conventions.
    The extracted value is the text after the last comma in the fullname,
    which TYPICALLY contains creator or institution info for MGI/RGD alleles,
    but this is not guaranteed.

    Args:
        fullname: The allele's full name (e.g., "targeted mutation 1.1, Joshua Scallan")
        taxon_id: The taxon CURIE (e.g., "NCBITaxon:10090" for mouse)

    Returns:
        Dict with value/confidence/source if extraction succeeded, None otherwise.
        - value: The extracted text
        - confidence: "probable" (2+ words, typical pattern) or "uncertain" (atypical)
        - source: "fullname_suffix" (always - explains provenance)

    Returns None when:
        - fullname is None or empty
        - MOD doesn't typically have attribution info (WB, SGD, ZFIN, FB)
        - No comma-separated suffix found
        - Fullname is uninformative (e.g., "wild type")
        - Extracted text is too short (< 4 chars)

    Examples (MGI):
        "targeted mutation 1.1, Joshua Scallan" → {"value": "Joshua Scallan", ...}
        "gene trap 460B7, Centre for Modeling Human Disease" → {"value": "Centre for...", ...}
        "wild type" → None

    Examples (RGD):
        "angiotensin II receptor; mutation 1, Medical College of Wisconsin" → {...}
    """
    if not fullname:
        return None

    # Determine MOD from taxon
    mod = TAXON_TO_PROVIDER.get(taxon_id)
    if not mod:
        logger.debug('Unknown taxon %s, skipping fullname attribution extraction', taxon_id)
        return None

    # Only attempt extraction for MODs known to have attribution info
    # Other MODs: WB/SGD have no fullnames, ZFIN has IDs, FB has descriptive names
    if mod not in MODS_WITH_FULLNAME_ATTRIBUTION:
        return None

    # Skip uninformative patterns
    if fullname.lower() == 'wild type':
        return None

    # Pattern: "..., Creator/Institution" at end of string
    # Matches: ", Firstname Lastname" or ", Institution Name With Spaces"
    # Allows: letters, spaces, &, ', - in the extracted portion
    match = re.search(r',\s+([A-Z][A-Za-z\s&\'\-]+)$', fullname)
    if match:
        extracted = match.group(1).strip()
        # Filter out very short matches (likely parsing errors)
        if len(extracted) >= 4:
            # Determine confidence based on pattern
            # Person names typically have 2-3 words, institutions have more
            word_count = len(extracted.split())
            confidence = "probable" if word_count >= 2 else "uncertain"

            return {
                "value": extracted,
                "confidence": confidence,
                "source": "fullname_suffix"
            }

    return None


def _ok(data: Any = None, count: Optional[int] = None, warnings: Optional[List[str]] = None, message: Optional[str] = None) -> AgrQueryResult:
    return AgrQueryResult(
        status="ok",
        data=data,
        count=count,
        warnings=warnings or None,
        message=message
    )


def _err(message: str) -> AgrQueryResult:
    return AgrQueryResult(status="error", message=message)


def _validation_warning(message: str) -> AgrQueryResult:
    """Return a validation warning response (not an error, but search not executed)."""
    return AgrQueryResult(status="validation_warning", message=message)


@function_tool
def agr_curation_query(
    method: str,
    gene_symbol: Optional[str] = None,
    gene_id: Optional[str] = None,
    allele_symbol: Optional[str] = None,
    allele_id: Optional[str] = None,
    data_provider: Optional[str] = None,
    taxon_id: Optional[str] = None,
    term: Optional[str] = None,
    go_aspect: Optional[str] = None,
    exact_match: bool = False,
    include_synonyms: bool = True,
    limit: Optional[int] = None,
    force: bool = False,
    force_reason: Optional[str] = None
) -> AgrQueryResult:
    """
    Query the Alliance Genome Resources Curation Database.

    IMPORTANT - Symbol Validation:
    Gene and allele symbol searches (search_genes, search_alleles) are validated
    before execution. If the symbol contains patterns that suggest genotype notation
    (whitespace, fl/fl, +/+, -/-), the tool returns a validation_warning instead of
    searching. This prevents searches that will definitely fail.

    To handle validation_warning:
    1. Extract the base symbol (remove genotype notation)
    2. Retry with the cleaned symbol
    3. Only use force=True if you're certain the exact string should be searched

    Args:
        method: The query method (search_genes, search_alleles, get_gene_by_id, etc.)
        gene_symbol: Gene symbol to search for
        gene_id: Gene ID/CURIE for direct lookup
        allele_symbol: Allele symbol to search for
        allele_id: Allele ID/CURIE for direct lookup
        data_provider: Filter by species (MGI, FB, WB, ZFIN, RGD, SGD, HGNC)
        taxon_id: Alternative to data_provider (NCBITaxon:XXXXX format)
        term: Search term for ontology searches
        go_aspect: GO aspect filter (molecular_function, biological_process, cellular_component)
        exact_match: Require exact match for ontology searches
        include_synonyms: Search synonyms in addition to primary symbols (default: True)
        limit: Maximum results to return
        force: Skip symbol validation (default: False). Requires force_reason.
        force_reason: Explanation for why validation is being skipped (required if force=True)

    Returns:
        AgrQueryResult with status='ok', 'error', or 'validation_warning'
    """
    try:
        db = get_agr_db_client()
        limit_value, warnings = _normalize_limit(limit)

        # Log query parameters for tracing
        logger.debug(
            '[agr_curation_query] method=%s, allele_symbol=%s, gene_symbol=%s, data_provider=%s, include_synonyms=%s',
            method,
            allele_symbol,
            gene_symbol,
            data_provider,
            include_synonyms,
        )

        # GET GENE BY EXACT SYMBOL (uses SQL IN clause - requires exact match)
        if method == "get_gene_by_exact_symbol":
            if not gene_symbol:
                return _err("get_gene_by_exact_symbol requires gene_symbol")

            if ':' in gene_symbol:
                prefix, symbol = gene_symbol.split(':', 1)
                if prefix in PROVIDER_TO_TAXON:
                    if not data_provider:
                        data_provider = prefix
                    gene_symbol = symbol

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(f"Unknown data_provider: {data_provider}. Valid: {list(PROVIDER_TO_TAXON.keys())}")
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            genes_data: List[Dict[str, Any]] = []
            for tid in taxon_ids:
                try:
                    results = db.map_entity_names_to_curies(
                        entity_type='gene',
                        entity_names=[gene_symbol],
                        taxon_curie=tid
                    )
                    for result in results:
                        try:
                            gene = db.get_gene(result['entity_curie'])
                            if gene:
                                genes_data.append({
                                    "curie": gene.primaryExternalId,
                                    "symbol": gene.geneSymbol.displayText if gene.geneSymbol else result['entity'],
                                    "name": gene.geneFullName.displayText if gene.geneFullName else None,
                                    "taxon": tid,
                                    "gene_type": gene.geneType.get("name") if gene.geneType and isinstance(gene.geneType, dict) else str(gene.geneType) if gene.geneType else None,
                                })
                        except Exception as e:
                            logger.warning('Failed to fetch gene %s: %s', result.get('entity_curie'), e)
                except Exception as e:
                    logger.warning('Failed to search taxon %s: %s', tid, e)

            validated_data = genes_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            return _ok(data=validated_data, count=len(validated_data), warnings=warnings)

        # SEARCH GENES (uses LIKE search - supports partial matches)
        elif method == "search_genes":
            if not gene_symbol:
                return _err("search_genes requires gene_symbol")

            # Validate symbol before searching (unless force=True)
            if not force:
                validation = validate_search_symbol(gene_symbol, 'gene')
                if not validation.is_valid:
                    return _validation_warning(validation.warning_message)
            else:
                # Check force_reason is provided
                force_valid, force_error = check_force_parameters(force, force_reason)
                if not force_valid:
                    return _err(force_error)
                # Log the override for tracing
                log_validation_override(gene_symbol, 'gene', force_reason)

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(f"Unknown data_provider: {data_provider}")
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            genes_data: List[Dict[str, Any]] = []
            for tid in taxon_ids:
                try:
                    results = db.search_entities(
                        entity_type='gene',
                        search_pattern=gene_symbol,
                        taxon_curie=tid,
                        include_synonyms=include_synonyms,
                        limit=limit_value
                    )
                    for result in results:
                        try:
                            gene = db.get_gene(result['entity_curie'])
                            if gene:
                                # Preserve what entity matched the search (may differ from primary symbol)
                                matched_entity = result['entity']
                                primary_symbol = gene.geneSymbol.displayText if gene.geneSymbol else matched_entity

                                gene_entry = {
                                    "curie": gene.primaryExternalId,
                                    "symbol": primary_symbol,
                                    "name": gene.geneFullName.displayText if gene.geneFullName else None,
                                    "taxon": tid,
                                    "match_type": result.get('match_type', 'unknown'),
                                }

                                # Add matched_on field if search matched a synonym
                                enrich_with_match_context(gene_entry, matched_entity, primary_symbol, 'gene')

                                genes_data.append(gene_entry)
                        except Exception as e:
                            logger.warning('Failed to fetch gene details: %s', e)
                except Exception as e:
                    logger.warning('Failed to fuzzy search taxon %s: %s', tid, e)

            validated_data = genes_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            return _ok(data=validated_data, count=len(validated_data), warnings=warnings)

        # GET GENE BY ID
        elif method == "get_gene_by_id":
            if not gene_id:
                return _err("get_gene_by_id requires gene_id")

            gene = db.get_gene(gene_id)
            if not gene:
                return _ok(data=None, message=f"Gene not found: {gene_id}")

            gene_dict = {
                "curie": gene.primaryExternalId,
                "symbol": gene.geneSymbol.displayText if gene.geneSymbol else None,
                "name": gene.geneFullName.displayText if gene.geneFullName else None,
                "taxon": gene.taxon,
                "gene_type": gene.geneType.get("name") if gene.geneType and isinstance(gene.geneType, dict) else str(gene.geneType) if gene.geneType else None,
            }

            if hasattr(gene, 'genomeLocations') and gene.genomeLocations:
                loc = gene.genomeLocations[0]
                gene_dict["genomic_location"] = {
                    "chromosome": loc.chromosome,
                    "start": loc.start,
                    "end": loc.end,
                    "strand": loc.strand,
                    "assembly": loc.assembly,
                }

            _validate_curie_in_result(gene_dict)
            return _ok(data=gene_dict)

        # GET ALLELE BY EXACT SYMBOL (uses SQL IN clause - requires exact match)
        elif method == "get_allele_by_exact_symbol":
            if not allele_symbol:
                return _err("get_allele_by_exact_symbol requires allele_symbol")

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(f"Unknown data_provider: {data_provider}")
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            # Normalize allele symbol: convert Gene<allele> to Gene<sup>allele</sup>
            symbol_variants = _normalize_allele_symbol_for_db(allele_symbol)
            if len(symbol_variants) > 1:
                logger.info("Normalized allele symbol '%s' to variants: %s", allele_symbol, symbol_variants)

            alleles_data: List[Dict[str, Any]] = []
            seen_curies = set()  # Avoid duplicates across variants
            for tid in taxon_ids:
                for symbol_variant in symbol_variants:
                    try:
                        results = db.map_entity_names_to_curies(
                            entity_type='allele',
                            entity_names=[symbol_variant],
                            taxon_curie=tid
                        )
                        for result in results:
                            try:
                                curie = result['entity_curie']
                                if curie in seen_curies:
                                    continue  # Skip duplicates
                                seen_curies.add(curie)

                                allele = db.get_allele(curie)
                                if allele:
                                    fullname = allele.alleleFullName.displayText if allele.alleleFullName else None
                                    alleles_data.append({
                                        "curie": allele.primaryExternalId,
                                        "symbol": allele.alleleSymbol.displayText if allele.alleleSymbol else result['entity'],
                                        "name": fullname,
                                        "taxon": tid,
                                        "matched_variant": symbol_variant,  # Track which variant matched
                                        "fullname_attribution": _extract_fullname_attribution(fullname, tid),
                                    })
                            except Exception as e:
                                logger.warning('Failed to fetch allele details: %s', e)
                    except Exception as e:
                        logger.warning("Failed to search alleles in taxon %s with variant '%s': %s", tid, symbol_variant, e)

            validated_data = alleles_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            return _ok(data=validated_data, count=len(validated_data), warnings=warnings)

        # SEARCH ALLELES (uses LIKE search - supports partial matches)
        elif method == "search_alleles":
            if not allele_symbol:
                return _err("search_alleles requires allele_symbol")

            # Validate symbol before searching (unless force=True)
            if not force:
                validation = validate_search_symbol(allele_symbol, 'allele')
                if not validation.is_valid:
                    return _validation_warning(validation.warning_message)
            else:
                # Check force_reason is provided
                force_valid, force_error = check_force_parameters(force, force_reason)
                if not force_valid:
                    return _err(force_error)
                # Log the override for tracing
                log_validation_override(allele_symbol, 'allele', force_reason)

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(f"Unknown data_provider: {data_provider}")
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            alleles_data: List[Dict[str, Any]] = []
            seen_curies = set()  # Avoid duplicates
            for tid in taxon_ids:
                try:
                    results = db.search_entities(
                        entity_type='allele',
                        search_pattern=allele_symbol,
                        taxon_curie=tid,
                        include_synonyms=include_synonyms,
                        limit=limit_value
                    )
                    for result in results:
                        try:
                            curie = result['entity_curie']
                            if curie in seen_curies:
                                continue  # Skip duplicates
                            seen_curies.add(curie)

                            allele = db.get_allele(curie)
                            if allele:
                                # Preserve what entity matched the search (may differ from primary symbol)
                                matched_entity = result['entity']
                                primary_symbol = allele.alleleSymbol.displayText if allele.alleleSymbol else matched_entity

                                fullname = allele.alleleFullName.displayText if allele.alleleFullName else None
                                allele_entry = {
                                    "curie": allele.primaryExternalId,
                                    "symbol": primary_symbol,
                                    "name": fullname,
                                    "taxon": tid,
                                    "match_type": result.get('match_type', 'unknown'),
                                    "fullname_attribution": _extract_fullname_attribution(fullname, tid),
                                }

                                # Add matched_on field if search matched a synonym
                                enrich_with_match_context(allele_entry, matched_entity, primary_symbol, 'allele')

                                alleles_data.append(allele_entry)
                        except Exception as e:
                            logger.warning('Failed to fetch allele details: %s', e)
                except Exception as e:
                    logger.warning('Failed to fuzzy search alleles in taxon %s: %s', tid, e)

            validated_data = alleles_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            # Log search results for tracing
            logger.debug(
                '[agr_curation_query] search_alleles returning %s results: %s',
                len(validated_data),
                [d.get('curie') for d in validated_data[:5]],
            )

            return _ok(data=validated_data, count=len(validated_data), warnings=warnings)

        # GET ALLELE BY ID
        elif method == "get_allele_by_id":
            if not allele_id:
                return _err("get_allele_by_id requires allele_id")

            allele = db.get_allele(allele_id)
            if not allele:
                return _ok(data=None, message=f"Allele not found: {allele_id}")

            fullname = allele.alleleFullName.displayText if allele.alleleFullName else None
            taxon = allele.taxon if hasattr(allele, 'taxon') else None
            allele_dict = {
                "curie": allele.primaryExternalId,
                "symbol": allele.alleleSymbol.displayText if allele.alleleSymbol else None,
                "name": fullname,
                "taxon": taxon,
                "fullname_attribution": _extract_fullname_attribution(fullname, taxon) if taxon else None,
            }
            _validate_curie_in_result(allele_dict)
            return _ok(data=allele_dict)

        # GET SPECIES
        elif method == "get_species":
            species_list = db.get_species()
            species_data = [{
                "abbreviation": s.abbreviation,
                "display_name": s.display_name,
            } for s in species_list]
            return _ok(data=species_data, count=len(species_data))

        # GET DATA PROVIDERS
        elif method == "get_data_providers":
            providers = db.get_data_providers()
            providers_data = [{"abbreviation": abbr, "taxon_id": taxon} for abbr, taxon in providers]
            return _ok(data=providers_data, count=len(providers_data))

        # ANATOMY TERMS SEARCH
        elif method == "search_anatomy_terms":
            if not term or not data_provider:
                return _err("search_anatomy_terms requires term and data_provider")

            results = db.search_anatomy_terms(
                term=term,
                data_provider=data_provider,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value
            )
            results_data = [{"curie": r.curie, "name": r.name, "ontology_type": r.ontology_type} for r in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []

            return _ok(data=results_data, count=len(results_data), warnings=validation_warnings)

        # LIFE STAGE TERMS SEARCH
        elif method == "search_life_stage_terms":
            if not term or not data_provider:
                return _err("search_life_stage_terms requires term and data_provider")

            results = db.search_life_stage_terms(
                term=term,
                data_provider=data_provider,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value
            )
            results_data = [{"curie": r.curie, "name": r.name, "ontology_type": r.ontology_type} for r in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []

            return _ok(data=results_data, count=len(results_data), warnings=validation_warnings)

        # GO TERMS SEARCH
        elif method == "search_go_terms":
            if not term:
                return _err("search_go_terms requires term")

            results = db.search_go_terms(
                term=term,
                go_aspect=go_aspect,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value
            )
            results_data = [{"curie": r.curie, "name": r.name, "namespace": r.namespace} for r in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []

            return _ok(data=results_data, count=len(results_data), warnings=validation_warnings)

        else:
            return _err(
                "Unknown method: {method}. Valid: "
                "search_genes, get_gene_by_exact_symbol, get_gene_by_id, "
                "search_alleles, get_allele_by_exact_symbol, get_allele_by_id, "
                "get_species, get_data_providers, "
                "search_anatomy_terms, search_life_stage_terms, search_go_terms".format(method=method)
            )

    except Exception as e:
        logger.error("AGR query error: %s", e, exc_info=True)
        return _err(f"Query error: {str(e)}")
