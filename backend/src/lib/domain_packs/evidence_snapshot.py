"""Resolve exact evidence inside one already-authorized artifact/checkpoint snapshot.

No database/global lookup and no latest-version pointer. Callers must authorize and
load the requested snapshot before constructing this index. A saved source record
and its display projection are not competing versions; contradictory known source
fields are. Never reconstruct identity from text, position, or supporting-object IDs.
"""
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

# Every source text/location form consumed by export, inspection and PDF anchors.
# Keep values exactly as saved: these keys select provenance, not object attachments
# or validation metadata. In particular, nested anchors are source-bearing too.
SOURCE_FIELDS = frozenset({
    "verified_quote", "quote", "source_quote", "evidence_quote", "snippet",
    "snippet_text", "sentence_text", "text", "normalized_text",
    "page", "page_number", "page_label", "section", "section_title", "source_section",
    "subsection", "subsection_title", "chunk_id", "chunk_ids", "source_chunk_id",
    "document_id", "source_document_id", "pdf_document_id", "source_span_ids",
    "source_fragments", "figure_reference", "table_reference", "anchor",
    "source_id", "source", "source_title", "title", "source_url", "url", "uri",
})
RECORD_LISTS = ("evidence_records", "evidence", "evidence_items", "evidence_anchors")


def evidence_records(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = []
    for key in RECORD_LISTS:
        entries = value.get(key)
        if isinstance(entries, list):
            records.extend(entry for entry in entries if isinstance(entry, Mapping))
    if isinstance(value.get("evidence_record_id"), str) and any(k in value for k in SOURCE_FIELDS):
        records.append(value)
    for key in ("metadata", "extraction_metadata", "payload"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            records.extend(evidence_records(nested))
    return records


class EvidenceSnapshot:
    def __init__(self, records: Sequence[Mapping[str, Any]]):
        self._records: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for record in records:
            identity = record.get("evidence_record_id")
            if isinstance(identity, str) and identity:
                self._records[identity].append(record)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]] = ()):
        records = evidence_records(payload)
        for item in items:
            records.extend(evidence_records(item))
        return cls(records)

    def resolve(self, identity: str) -> tuple[dict[str, Any] | None, str | None]:
        records = self._records.get(identity, [])
        if not records:
            return None, "missing"
        # Null optional fields and omitted optional fields carry no competing value.
        sources = [{k: r[k] for k in SOURCE_FIELDS if r.get(k) is not None} for r in records]
        known: dict[str, Any] = {}
        for source in sources:
            for key, value in source.items():
                if key in known and known[key] != value:
                    return None, "ambiguous"
                known[key] = value
        # Return an actual saved record containing all known provenance, never a
        # synthetic merge of partial source records. Rich canonical records win
        # over faithful quote projections without discarding conflicting values.
        for record, source in zip(records, sources, strict=True):
            if source == known:
                return deepcopy(dict(record)), None
        return None, "ambiguous"
