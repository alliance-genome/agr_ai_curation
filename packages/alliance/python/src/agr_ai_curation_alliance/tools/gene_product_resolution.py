"""Typed Alliance gene and mature-RNA product resolution.

RGD locus identity remains owned by the read-only Alliance curation database.
RNAcentral supplies species-specific RNA identity, while its miRBase cross-references
ground mature and precursor accessions.  A mature product is mapped back to every
curation gene carrying that RNAcentral cross-reference; this module never selects
one of multiple precursor loci.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, Literal

import requests
from agents import function_tool
from pydantic import BaseModel, ConfigDict, Field

from agr_ai_curation_runtime import get_curation_resolver

from .agr_lookup import create_db_session
from .go_annotations import validate_go_gene_id

logger = logging.getLogger(__name__)

_RNACENTRAL_SEARCH_URL = "https://www.ebi.ac.uk/ebisearch/ws/rest/rnacentral"
_RNACENTRAL_API_BASE = "https://rnacentral.org/api/v1"
_TAXON_PATTERN = re.compile(r"NCBITaxon:(\d+)")
_PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9._-]*")
_RNACENTRAL_ID_PATTERN = re.compile(r"URS[0-9A-F]{10}_(\d+)")
_MIRBASE_MATURE_PATTERN = re.compile(r"MIMAT\d+")
_MIRBASE_HAIRPIN_PATTERN = re.compile(r"MI\d+")

ResolutionStatus = Literal["resolved", "ambiguous", "not_found", "upstream_error"]
IdentityKind = Literal[
    "mature_product",
    "precursor_locus",
    "ordinary_gene",
    "unknown",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolutionProvenance(_StrictModel):
    """One source record used in the resolution decision."""

    source: Literal["Alliance curation database", "RNAcentral", "miRBase"]
    source_url: str
    source_record_id: str
    evidence: str


class MatureProductIdentity(_StrictModel):
    """Species-specific mature product identity, separate from precursor loci."""

    label: str
    organism_taxon_id: str
    rnacentral_id: str
    mirbase_id: str | None = None


class GeneProductCandidate(_StrictModel):
    """One evidence-backed Alliance gene/locus mapping safe for GO lookup."""

    gene_id: str
    symbol: str | None = None
    name: str | None = None
    identity_kind: Literal["precursor_locus", "ordinary_gene"]
    organism_taxon_id: str
    gene_type: str | None = None
    rnacentral_ids: list[str] = Field(default_factory=list)
    mirbase_hairpin_ids: list[str] = Field(default_factory=list)
    provenance: list[ResolutionProvenance] = Field(default_factory=list)


class GeneProductResolution(_StrictModel):
    """Stable result for every gene/product resolution outcome."""

    status: ResolutionStatus
    query: str
    identity_kind: IdentityKind
    organism_taxon_id: str
    provider_prefix: str
    resolved_gene_id: str | None = None
    mature_product: MatureProductIdentity | None = None
    product_candidates: list[MatureProductIdentity] = Field(default_factory=list)
    candidate_mappings: list[GeneProductCandidate] = Field(default_factory=list)
    candidate_limit_reached: bool = False
    provenance: list[ResolutionProvenance] = Field(default_factory=list)
    message: str


_CacheKey = tuple[str, str, str, str]
_CACHE: OrderedDict[_CacheKey, tuple[float, GeneProductResolution]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        value = default
    return max(minimum, value)


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        value = default
    return max(minimum, value)


def _request_timeout_seconds() -> float:
    return _env_float("RNA_GENE_PRODUCT_REQUEST_TIMEOUT_SECONDS", 10.0, minimum=0.1)


def _cache_ttl_seconds() -> float:
    return _env_float("RNA_GENE_PRODUCT_CACHE_TTL_SECONDS", 300.0, minimum=0.0)


def _cache_max_entries() -> int:
    return _env_int("RNA_GENE_PRODUCT_CACHE_MAX_ENTRIES", 256, minimum=1)


def _max_candidates() -> int:
    return _env_int("RNA_GENE_PRODUCT_MAX_CANDIDATES", 25, minimum=1)


def clear_gene_product_resolution_cache() -> None:
    """Clear process-local resolver results, primarily for deterministic tests."""

    with _CACHE_LOCK:
        _CACHE.clear()


def _cached(key: _CacheKey) -> GeneProductResolution | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is None:
            return None
        expires_at, result = item
        if expires_at <= now:
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return result.model_copy(deep=True)


def _store_cached(key: _CacheKey, result: GeneProductResolution) -> None:
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        return
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl, result.model_copy(deep=True))
        _CACHE.move_to_end(key)
        while len(_CACHE) > _cache_max_entries():
            _CACHE.popitem(last=False)


def _source_url_for_gene(gene_id: str) -> str:
    prefix, local_id = gene_id.split(":", 1)
    if prefix == "RGD":
        return f"https://rgd.mcw.edu/rgdweb/report/gene/main.html?id={local_id}"
    return f"https://www.alliancegenome.org/gene/{gene_id}"


def _candidate_from_row(row: Mapping[str, Any]) -> GeneProductCandidate:
    gene_id = str(row["gene_id"])
    name = row.get("gene_name")
    identity_kind: Literal["precursor_locus", "ordinary_gene"] = (
        "precursor_locus"
        if str(row.get("gene_type") or "").casefold() == "ncrna_gene"
        and str(name or "").casefold().startswith("microrna ")
        else "ordinary_gene"
    )
    rnacentral_ids = sorted(
        {
            str(value)
            for value in (row.get("rnacentral_ids") or [])
            if isinstance(value, str) and value.startswith("RNAcentral:URS")
        }
    )
    return GeneProductCandidate(
        gene_id=gene_id,
        symbol=row.get("gene_symbol"),
        name=name,
        identity_kind=identity_kind,
        organism_taxon_id=str(row["taxon_id"]),
        gene_type=row.get("gene_type"),
        rnacentral_ids=rnacentral_ids,
        provenance=[
            ResolutionProvenance(
                source="Alliance curation database",
                source_url=_source_url_for_gene(gene_id),
                source_record_id=gene_id,
                evidence="Current non-obsolete gene/locus identity and RNAcentral cross-references",
            )
        ],
    )


def _validated_candidates(
    candidates: list[GeneProductCandidate],
) -> list[GeneProductCandidate]:
    safe = [
        candidate
        for candidate in candidates
        if validate_go_gene_id(candidate.gene_id) == candidate.gene_id
    ]
    if len(safe) != len(candidates):
        raise ValueError(
            "Alliance curation lookup returned a gene CURIE not accepted by go_api_call"
        )
    return safe


def _query_curation_database(
    *,
    query: str | None,
    organism_taxon_id: str,
    provider_prefix: str,
    rnacentral_id: str | None,
) -> list[GeneProductCandidate]:
    resolver = get_curation_resolver()
    db = resolver.get_db_client()
    if db is None:
        raise RuntimeError("Alliance curation database client is unavailable")
    session = create_db_session(db)
    if session is None:
        raise RuntimeError("Alliance curation database session is unavailable")

    from sqlalchemy import text

    row_limit = _max_candidates() + 1
    match_clause = ""
    params: dict[str, Any] = {
        "taxon_id": organism_taxon_id,
        "provider_pattern": f"{provider_prefix}:%",
        "row_limit": row_limit,
    }
    if rnacentral_id is not None:
        match_clause = """
            AND EXISTS (
                SELECT 1
                FROM genomicentity_crossreference mapping_gec
                JOIN crossreference mapping_cr
                  ON mapping_cr.id = mapping_gec.crossreferences_id
                WHERE mapping_gec.genomicentity_id = be.id
                  AND mapping_cr.referencedcurie = :rnacentral_id
                  AND mapping_cr.obsolete = false
            )
        """
        params["rnacentral_id"] = rnacentral_id
    else:
        match_clause = """
            AND (
                lower(be.primaryexternalid) = lower(:query)
                OR lower(symbol.displaytext) = lower(:query)
                OR lower(fullname.displaytext) = lower(:query)
            )
        """
        params["query"] = query

    sql = text(
        f"""
        SELECT
            be.primaryexternalid AS gene_id,
            symbol.displaytext AS gene_symbol,
            fullname.displaytext AS gene_name,
            taxon.curie AS taxon_id,
            gt.name AS gene_type,
            array_remove(
                array_agg(DISTINCT CASE
                    WHEN all_cr.referencedcurie LIKE 'RNAcentral:URS%'
                    THEN all_cr.referencedcurie
                END),
                NULL
            ) AS rnacentral_ids
        FROM biologicalentity be
        JOIN gene g ON g.id = be.id
        JOIN ontologyterm taxon ON taxon.id = be.taxon_id
        LEFT JOIN ontologyterm gt ON gt.id = g.genetype_id
        LEFT JOIN slotannotation symbol
          ON symbol.singlegene_id = g.id
         AND symbol.slotannotationtype = 'GeneSymbolSlotAnnotation'
         AND symbol.obsolete = false
        LEFT JOIN slotannotation fullname
          ON fullname.singlegene_id = g.id
         AND fullname.slotannotationtype = 'GeneFullNameSlotAnnotation'
         AND fullname.obsolete = false
        LEFT JOIN genomicentity_crossreference all_gec
          ON all_gec.genomicentity_id = be.id
        LEFT JOIN crossreference all_cr
          ON all_cr.id = all_gec.crossreferences_id
         AND all_cr.obsolete = false
        WHERE be.obsolete = false
          AND be.internal = false
          AND taxon.curie = :taxon_id
          AND be.primaryexternalid LIKE :provider_pattern
          {match_clause}
        GROUP BY
            be.primaryexternalid,
            symbol.displaytext,
            fullname.displaytext,
            taxon.curie,
            gt.name
        ORDER BY be.primaryexternalid
        LIMIT :row_limit
        """
    )
    try:
        rows = session.execute(sql, params).mappings().all()
    finally:
        session.close()
    return [_candidate_from_row(row) for row in rows]


def _json_response(
    requester: Callable[..., Any],
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    response = requester(
        url,
        params=dict(params or {}),
        headers={"Accept": "application/json"},
        timeout=_request_timeout_seconds(),
    )
    if not 200 <= response.status_code < 300:
        raise requests.HTTPError(
            f"RNA source returned HTTP {response.status_code}", response=response
        )
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("RNA source response must be an object")
    return payload


def _mature_label(query: str, mirbase_organism_prefix: str) -> str:
    if query.casefold().startswith(f"{mirbase_organism_prefix.casefold()}-"):
        return query
    return f"{mirbase_organism_prefix}-{query}"


def _rnacentral_xrefs(
    *,
    urs: str,
    taxon_number: str,
    requester: Callable[..., Any],
) -> tuple[list[Mapping[str, Any]], bool]:
    xref_url = f"{_RNACENTRAL_API_BASE}/rna/{urs}/xrefs/{taxon_number}/"
    payload = _json_response(
        requester,
        xref_url,
        params={"page_size": _max_candidates() + 1},
    )
    raw_xrefs = payload.get("results")
    count = payload.get("count")
    if not isinstance(raw_xrefs, list):
        raise ValueError("RNAcentral xref response must contain a results list")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < len(raw_xrefs)
    ):
        raise ValueError("RNAcentral xref response must contain a valid count")
    if not all(isinstance(xref, Mapping) for xref in raw_xrefs):
        raise ValueError("RNAcentral xref result must be an object")
    source_incomplete = payload.get("next") is not None or count > len(raw_xrefs)
    return list(raw_xrefs), source_incomplete


def _search_mature_products(
    *,
    query: str,
    organism_taxon_id: str,
    mirbase_organism_prefix: str,
    requester: Callable[..., Any],
) -> tuple[list[MatureProductIdentity], list[ResolutionProvenance], bool]:
    taxon = _TAXON_PATTERN.fullmatch(organism_taxon_id)
    assert taxon is not None
    taxon_number = taxon.group(1)
    label = _mature_label(query, mirbase_organism_prefix)
    payload = _json_response(
        requester,
        _RNACENTRAL_SEARCH_URL,
        params={
            "query": f'"{label}"',
            "format": "json",
            "size": _max_candidates() + 1,
            "fields": "id,description,rna_type,expert_db",
        },
    )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("RNAcentral search response must contain an entries list")
    hit_count = payload.get("hitCount")
    if (
        not isinstance(hit_count, int)
        or isinstance(hit_count, bool)
        or hit_count < len(entries)
    ):
        raise ValueError("RNAcentral search response must contain a valid hitCount")
    source_incomplete = hit_count > len(entries)

    products: list[MatureProductIdentity] = []
    provenance: list[ResolutionProvenance] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
            raise ValueError("RNAcentral search entry must contain an id")
        match = _RNACENTRAL_ID_PATTERN.fullmatch(entry["id"])
        fields = entry.get("fields")
        if (
            match is None
            or match.group(1) != taxon_number
            or not isinstance(fields, Mapping)
        ):
            continue
        descriptions = fields.get("description")
        rna_types = fields.get("rna_type")
        expert_dbs = fields.get("expert_db")
        if (
            not isinstance(descriptions, list)
            or not isinstance(rna_types, list)
            or not isinstance(expert_dbs, list)
        ):
            raise ValueError("RNAcentral search entry fields have an invalid contract")
        if not any(str(value).casefold() == "mirna" for value in rna_types):
            continue
        if not any(str(value).casefold() == "mirbase" for value in expert_dbs):
            continue
        if not any(
            str(value).casefold().endswith(label.casefold()) for value in descriptions
        ):
            continue
        product = MatureProductIdentity(
            label=label,
            organism_taxon_id=organism_taxon_id,
            rnacentral_id=f"RNAcentral:{entry['id']}",
        )
        products.append(product)
        provenance.append(
            ResolutionProvenance(
                source="RNAcentral",
                source_url=f"https://rnacentral.org/rna/{entry['id']}",
                source_record_id=product.rnacentral_id,
                evidence="Exact species-specific mature miRNA search match",
            )
        )

    if len(products) != 1:
        return products, provenance, source_incomplete

    product = products[0]
    urs = product.rnacentral_id.split(":", 1)[1].split("_", 1)[0]
    xrefs, xrefs_incomplete = _rnacentral_xrefs(
        urs=urs,
        taxon_number=taxon_number,
        requester=requester,
    )
    source_incomplete = source_incomplete or xrefs_incomplete
    mirbase_ids: set[str] = set()
    for xref in xrefs:
        if str(xref.get("database", "")).casefold() != "mirbase" or not xref.get(
            "is_active"
        ):
            continue
        accession = xref.get("accession")
        if not isinstance(accession, Mapping):
            raise ValueError("miRBase xref must contain accession details")
        external_id = accession.get("external_id")
        optional_id = accession.get("optional_id")
        if (
            isinstance(external_id, str)
            and _MIRBASE_MATURE_PATTERN.fullmatch(external_id) is not None
            and isinstance(optional_id, str)
            and optional_id.casefold() == label.casefold()
        ):
            mirbase_ids.add(f"miRBase:{external_id}")

    if len(mirbase_ids) == 1:
        mirbase_id = next(iter(mirbase_ids))
        products[0] = product.model_copy(update={"mirbase_id": mirbase_id})
        provenance.append(
            ResolutionProvenance(
                source="miRBase",
                source_url=f"https://www.mirbase.org/mature/{mirbase_id.split(':', 1)[1]}",
                source_record_id=mirbase_id,
                evidence="Active mature miRNA accession exposed by RNAcentral cross-reference",
            )
        )
    elif len(mirbase_ids) > 1:
        products.extend(
            product.model_copy(update={"mirbase_id": mirbase_id})
            for mirbase_id in sorted(mirbase_ids)
        )
        products.pop(0)
    return products, provenance, source_incomplete


def _enrich_precursor_candidates(
    candidates: list[GeneProductCandidate],
    *,
    organism_taxon_id: str,
    requester: Callable[..., Any],
) -> tuple[list[GeneProductCandidate], bool]:
    """Attach every bounded active miRBase hairpin identity to precursor loci."""

    taxon = _TAXON_PATTERN.fullmatch(organism_taxon_id)
    assert taxon is not None
    taxon_number = taxon.group(1)
    candidate_limit = _max_candidates()
    enriched: list[GeneProductCandidate] = []
    source_incomplete = False
    xref_cache: dict[str, tuple[list[Mapping[str, Any]], bool]] = {}

    for candidate in candidates:
        if candidate.identity_kind != "precursor_locus":
            enriched.append(candidate)
            continue

        rnacentral_ids = candidate.rnacentral_ids
        if len(rnacentral_ids) > candidate_limit:
            source_incomplete = True
            rnacentral_ids = rnacentral_ids[:candidate_limit]

        hairpin_ids: set[str] = set()
        hairpin_provenance: list[ResolutionProvenance] = []
        for rnacentral_id in rnacentral_ids:
            urs = rnacentral_id.split(":", 1)[1].split("_", 1)[0]
            cached_xrefs = xref_cache.get(urs)
            if cached_xrefs is None:
                cached_xrefs = _rnacentral_xrefs(
                    urs=urs,
                    taxon_number=taxon_number,
                    requester=requester,
                )
                xref_cache[urs] = cached_xrefs
            xrefs, xrefs_incomplete = cached_xrefs
            source_incomplete = source_incomplete or xrefs_incomplete
            for xref in xrefs:
                if str(xref.get("database", "")).casefold() != "mirbase" or not xref.get(
                    "is_active"
                ):
                    continue
                accession = xref.get("accession")
                if not isinstance(accession, Mapping):
                    raise ValueError("miRBase xref must contain accession details")
                external_id = accession.get("external_id")
                if (
                    not isinstance(external_id, str)
                    or _MIRBASE_HAIRPIN_PATTERN.fullmatch(external_id) is None
                ):
                    continue
                mirbase_id = f"miRBase:{external_id}"
                if mirbase_id in hairpin_ids:
                    continue
                hairpin_ids.add(mirbase_id)
                hairpin_provenance.extend(
                    [
                        ResolutionProvenance(
                            source="RNAcentral",
                            source_url=f"https://rnacentral.org/rna/{urs}",
                            source_record_id=f"RNAcentral:{urs}",
                            evidence="Species-scoped precursor RNA record with an active miRBase cross-reference",
                        ),
                        ResolutionProvenance(
                            source="miRBase",
                            source_url=f"https://www.mirbase.org/hairpin/{external_id}",
                            source_record_id=mirbase_id,
                            evidence="Active precursor hairpin accession exposed by RNAcentral cross-reference",
                        ),
                    ]
                )

        enriched.append(
            candidate.model_copy(
                update={
                    "mirbase_hairpin_ids": sorted(hairpin_ids),
                    "provenance": [*candidate.provenance, *hairpin_provenance],
                }
            )
        )

    return enriched, source_incomplete


def _base_result(
    *,
    status: ResolutionStatus,
    query: str,
    identity_kind: IdentityKind,
    organism_taxon_id: str,
    provider_prefix: str,
    message: str,
    **values: Any,
) -> GeneProductResolution:
    return GeneProductResolution(
        status=status,
        query=query,
        identity_kind=identity_kind,
        organism_taxon_id=organism_taxon_id,
        provider_prefix=provider_prefix,
        message=message,
        **values,
    )


def resolve_gene_product(
    query: object,
    organism_taxon_id: object,
    provider_prefix: object,
    mirbase_organism_prefix: object,
    *,
    requester: Callable[..., Any] = requests.get,
    curation_lookup: Callable[..., list[GeneProductCandidate]] = _query_curation_database,
    use_cache: bool = True,
) -> GeneProductResolution:
    """Resolve a gene, precursor locus, or mature RNA product without guessing."""

    values = (query, organism_taxon_id, provider_prefix, mirbase_organism_prefix)
    if not all(
        isinstance(value, str) and value and value == value.strip()
        for value in values
    ):
        return _base_result(
            status="not_found",
            query=str(query) if isinstance(query, str) else "",
            identity_kind="unknown",
            organism_taxon_id=(
                str(organism_taxon_id) if isinstance(organism_taxon_id, str) else ""
            ),
            provider_prefix=(
                str(provider_prefix) if isinstance(provider_prefix, str) else ""
            ),
            message="All resolver inputs must be non-empty strings without surrounding whitespace",
        )
    assert isinstance(query, str)
    assert isinstance(organism_taxon_id, str)
    assert isinstance(provider_prefix, str)
    assert isinstance(mirbase_organism_prefix, str)

    if (
        _TAXON_PATTERN.fullmatch(organism_taxon_id) is None
        or _PROVIDER_PATTERN.fullmatch(provider_prefix) is None
    ):
        return _base_result(
            status="not_found",
            query=query,
            identity_kind="unknown",
            organism_taxon_id=organism_taxon_id,
            provider_prefix=provider_prefix,
            message="Taxon or provider input is not a valid resolver scope",
        )
    if query.startswith(f"{provider_prefix}:"):
        validation = validate_go_gene_id(query)
        if not isinstance(validation, str) or validation != query:
            return _base_result(
                status="not_found",
                query=query,
                identity_kind="unknown",
                organism_taxon_id=organism_taxon_id,
                provider_prefix=provider_prefix,
                message=f"Rejected synthetic or invalid {provider_prefix} gene CURIE",
            )

    cache_key = (query.casefold(), organism_taxon_id, provider_prefix, mirbase_organism_prefix)
    if use_cache and requester is requests.get and curation_lookup is _query_curation_database:
        cached = _cached(cache_key)
        if cached is not None:
            return cached

    try:
        exact_candidates = curation_lookup(
            query=query,
            organism_taxon_id=organism_taxon_id,
            provider_prefix=provider_prefix,
            rnacentral_id=None,
        )
    except Exception as exc:
        result = _base_result(
            status="upstream_error",
            query=query,
            identity_kind="unknown",
            organism_taxon_id=organism_taxon_id,
            provider_prefix=provider_prefix,
            message=f"Alliance curation database lookup failed: {exc}",
        )
    else:
        try:
            safe_exact = _validated_candidates(exact_candidates)
        except ValueError as exc:
            return _base_result(
                status="upstream_error",
                query=query,
                identity_kind="unknown",
                organism_taxon_id=organism_taxon_id,
                provider_prefix=provider_prefix,
                message=f"Alliance curation database contract failed: {exc}",
            )
        try:
            safe_exact, precursor_source_incomplete = _enrich_precursor_candidates(
                safe_exact,
                organism_taxon_id=organism_taxon_id,
                requester=requester,
            )
        except Exception as exc:
            return _base_result(
                status="upstream_error",
                query=query,
                identity_kind="precursor_locus",
                organism_taxon_id=organism_taxon_id,
                provider_prefix=provider_prefix,
                message=f"Precursor RNA source lookup failed: {exc}",
            )
        if len(safe_exact) == 1:
            candidate = safe_exact[0]
            if precursor_source_incomplete:
                result = _base_result(
                    status="ambiguous",
                    query=query,
                    identity_kind=candidate.identity_kind,
                    organism_taxon_id=organism_taxon_id,
                    provider_prefix=provider_prefix,
                    candidate_mappings=safe_exact,
                    candidate_limit_reached=True,
                    provenance=candidate.provenance,
                    message="Alliance locus was unique, but bounded precursor source evidence was incomplete",
                )
            else:
                result = _base_result(
                    status="resolved",
                    query=query,
                    identity_kind=candidate.identity_kind,
                    organism_taxon_id=organism_taxon_id,
                    provider_prefix=provider_prefix,
                    resolved_gene_id=candidate.gene_id,
                    candidate_mappings=safe_exact,
                    provenance=candidate.provenance,
                    message="Resolved one current Alliance gene/locus identity",
                )
        elif len(safe_exact) > 1:
            candidate_limit = _max_candidates()
            result = _base_result(
                status="ambiguous",
                query=query,
                identity_kind="unknown",
                organism_taxon_id=organism_taxon_id,
                provider_prefix=provider_prefix,
                candidate_mappings=safe_exact[:candidate_limit],
                candidate_limit_reached=(
                    len(safe_exact) > candidate_limit or precursor_source_incomplete
                ),
                provenance=[item for candidate in safe_exact for item in candidate.provenance],
                message="Multiple exact Alliance gene/locus identities matched; none was selected",
            )
        else:
            try:
                products, product_provenance, product_source_incomplete = _search_mature_products(
                    query=query,
                    organism_taxon_id=organism_taxon_id,
                    mirbase_organism_prefix=mirbase_organism_prefix,
                    requester=requester,
                )
                if product_source_incomplete:
                    candidate_limit = _max_candidates()
                    result = _base_result(
                        status="ambiguous",
                        query=query,
                        identity_kind="mature_product",
                        organism_taxon_id=organism_taxon_id,
                        provider_prefix=provider_prefix,
                        mature_product=products[0] if len(products) == 1 else None,
                        product_candidates=products[:candidate_limit],
                        candidate_limit_reached=True,
                        provenance=product_provenance,
                        message="Bounded RNAcentral source evidence was incomplete; product identity was not selected",
                    )
                elif not products:
                    result = _base_result(
                        status="not_found",
                        query=query,
                        identity_kind="unknown",
                        organism_taxon_id=organism_taxon_id,
                        provider_prefix=provider_prefix,
                        provenance=product_provenance,
                        message="No exact Alliance locus or source-grounded mature RNA product was found",
                    )
                elif len(products) != 1:
                    candidate_limit = _max_candidates()
                    result = _base_result(
                        status="ambiguous",
                        query=query,
                        identity_kind="mature_product",
                        organism_taxon_id=organism_taxon_id,
                        provider_prefix=provider_prefix,
                        product_candidates=products[:candidate_limit],
                        candidate_limit_reached=len(products) > candidate_limit,
                        provenance=product_provenance,
                        message="Mature product identity was not unique across RNAcentral and miRBase",
                    )
                elif products[0].mirbase_id is None:
                    result = _base_result(
                        status="not_found",
                        query=query,
                        identity_kind="mature_product",
                        organism_taxon_id=organism_taxon_id,
                        provider_prefix=provider_prefix,
                        mature_product=products[0],
                        product_candidates=products,
                        provenance=product_provenance,
                        message="RNAcentral identity was found without an active matching miRBase mature accession",
                    )
                else:
                    product = products[0]
                    base_rnacentral_id = product.rnacentral_id.split("_", 1)[0]
                    mapped = curation_lookup(
                        query=None,
                        organism_taxon_id=organism_taxon_id,
                        provider_prefix=provider_prefix,
                        rnacentral_id=base_rnacentral_id,
                    )
                    safe_mapped = _validated_candidates(mapped)
                    safe_mapped, precursor_source_incomplete = _enrich_precursor_candidates(
                        safe_mapped,
                        organism_taxon_id=organism_taxon_id,
                        requester=requester,
                    )
                    provenance = [
                        *product_provenance,
                        *(item for candidate in safe_mapped for item in candidate.provenance),
                    ]
                    if precursor_source_incomplete:
                        candidate_limit = _max_candidates()
                        result = _base_result(
                            status="ambiguous",
                            query=query,
                            identity_kind="mature_product",
                            organism_taxon_id=organism_taxon_id,
                            provider_prefix=provider_prefix,
                            mature_product=product,
                            product_candidates=[product],
                            candidate_mappings=safe_mapped[:candidate_limit],
                            candidate_limit_reached=True,
                            provenance=provenance,
                            message="Bounded precursor source evidence was incomplete; no gene/locus mapping was selected",
                        )
                    elif len(safe_mapped) == 1:
                        result = _base_result(
                            status="resolved",
                            query=query,
                            identity_kind="mature_product",
                            organism_taxon_id=organism_taxon_id,
                            provider_prefix=provider_prefix,
                            resolved_gene_id=safe_mapped[0].gene_id,
                            mature_product=product,
                            product_candidates=[product],
                            candidate_mappings=safe_mapped,
                            provenance=provenance,
                            message="Mature RNA product has one evidence-backed Alliance gene/locus mapping",
                        )
                    elif len(safe_mapped) > 1:
                        candidate_limit = _max_candidates()
                        result = _base_result(
                            status="ambiguous",
                            query=query,
                            identity_kind="mature_product",
                            organism_taxon_id=organism_taxon_id,
                            provider_prefix=provider_prefix,
                            mature_product=product,
                            product_candidates=[product],
                            candidate_mappings=safe_mapped[:candidate_limit],
                            candidate_limit_reached=len(safe_mapped) > candidate_limit,
                            provenance=provenance,
                            message="Mature RNA product maps to multiple precursor loci; none was selected",
                        )
                    else:
                        result = _base_result(
                            status="not_found",
                            query=query,
                            identity_kind="mature_product",
                            organism_taxon_id=organism_taxon_id,
                            provider_prefix=provider_prefix,
                            mature_product=product,
                            product_candidates=[product],
                            provenance=product_provenance,
                            message="Mature RNA identity was found, but no safe Alliance gene/locus mapping exists",
                        )
            except Exception as exc:
                result = _base_result(
                    status="upstream_error",
                    query=query,
                    identity_kind="unknown",
                    organism_taxon_id=organism_taxon_id,
                    provider_prefix=provider_prefix,
                    message=f"RNA product source lookup failed: {exc}",
                )

    if (
        result.status != "upstream_error"
        and use_cache
        and requester is requests.get
        and curation_lookup is _query_curation_database
    ):
        _store_cached(cache_key, result)
    return result


@function_tool(
    name_override="resolve_gene_product",
    description_override=(
        "Resolve an organism-scoped gene, precursor locus, or mature RNA product "
        "to evidence-backed Alliance gene CURIE candidates without guessing."
    ),
)
def resolve_gene_product_tool(
    query: str,
    organism_taxon_id: str,
    provider_prefix: str,
    mirbase_organism_prefix: str,
) -> GeneProductResolution:
    """Expose typed gene-product resolution as a package tool."""

    return resolve_gene_product(
        query,
        organism_taxon_id,
        provider_prefix,
        mirbase_organism_prefix,
    )


__all__ = [
    "GeneProductCandidate",
    "GeneProductResolution",
    "MatureProductIdentity",
    "ResolutionProvenance",
    "clear_gene_product_resolution_cache",
    "resolve_gene_product",
    "resolve_gene_product_tool",
]
