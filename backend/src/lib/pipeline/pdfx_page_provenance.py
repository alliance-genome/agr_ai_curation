"""Validate and index PDFX merged Markdown page provenance."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping


_SCHEMA = "pdfx-merged-page-provenance"
_CONTRACT_VERSION = "merged-page-provenance-v1"
_METHODS = frozenset(
    {
        "direct",
        "native_start_page",
        "deterministic_owner",
        "aligned_agreement",
        "llm_selected",
        "deterministic_fallback",
    }
)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "contract_version",
        "pdf_sha256",
        "merged_markdown_sha256",
        "merged_markdown_size_bytes",
        "audit_sha256",
        "merge_contract_id",
        "source_map_sha256",
        "expected_page_count",
        "ranges",
        "summary",
        "llm_receipts",
        "record_sha256",
    }
)
_RANGE_FIELDS = frozenset(
    {
        "byte_start",
        "byte_end",
        "page_number",
        "candidate_pages",
        "method",
        "source",
        "operation",
        "evidence_digest",
        "range_id",
    }
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class _PageRange:
    byte_start: int
    byte_end: int
    page_number: int


@dataclass(frozen=True, slots=True)
class MergedPageProvenance:
    """Validated ordered page ranges for exact merged Markdown bytes."""

    ranges: tuple[_PageRange, ...]
    range_starts: tuple[int, ...]
    markdown_size_bytes: int
    schema: str
    contract_version: str
    record_sha256: str
    expected_page_count: int
    summary: Mapping[str, Any]

    def page_for_byte_offset(self, byte_offset: int) -> int:
        """Return the page owning one zero-based merged-Markdown byte."""

        if type(byte_offset) is not int or not 0 <= byte_offset < self.markdown_size_bytes:
            raise ValueError("page provenance byte offset is out of bounds")
        index = bisect_right(self.range_starts, byte_offset) - 1
        if index < 0 or byte_offset >= self.ranges[index].byte_end:
            raise ValueError("page provenance byte offset is not covered")
        return self.ranges[index].page_number

    def receipt(self) -> dict[str, Any]:
        """Return compact durable evidence without duplicating all ranges."""

        return {
            "schema": self.schema,
            "contract_version": self.contract_version,
            "record_sha256": self.record_sha256,
            "expected_page_count": self.expected_page_count,
            "range_count": len(self.ranges),
            "summary": dict(self.summary),
        }


def parse_merged_page_provenance(
    raw: bytes,
    *,
    merged_markdown: bytes,
) -> MergedPageProvenance:
    """Validate the public PDFX v1 sidecar against exact Markdown bytes."""

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("page provenance JSON is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_FIELDS:
        raise ValueError("page provenance fields are invalid")
    if payload["schema"] != _SCHEMA or payload["contract_version"] != _CONTRACT_VERSION:
        raise ValueError("page provenance schema or contract is invalid")

    if not merged_markdown:
        raise ValueError("merged Markdown is empty")
    if (
        payload["merged_markdown_sha256"] != _sha256(merged_markdown)
        or payload["merged_markdown_size_bytes"] != len(merged_markdown)
    ):
        raise ValueError("page provenance merged Markdown binding is invalid")

    source_maps = payload["source_map_sha256"]
    if (
        not _is_sha256(payload["pdf_sha256"])
        or not _is_sha256(payload["merged_markdown_sha256"])
        or not _is_sha256(payload["audit_sha256"])
        or not isinstance(source_maps, dict)
        or not source_maps
        or any(
            not isinstance(source, str)
            or not source
            or not _is_sha256(digest)
            for source, digest in source_maps.items()
        )
        or not isinstance(payload["merge_contract_id"], str)
        or not payload["merge_contract_id"]
    ):
        raise ValueError("page provenance digest binding is invalid")

    page_count = payload["expected_page_count"]
    ranges = payload["ranges"]
    if type(page_count) is not int or page_count < 1:
        raise ValueError("page provenance page count is invalid")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("page provenance ranges are invalid")

    cursor = 0
    validated_ranges: list[_PageRange] = []
    method_counts: Counter[str] = Counter()
    method_bytes: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    source_bytes: Counter[str] = Counter()
    for item in ranges:
        if not isinstance(item, Mapping) or set(item) != _RANGE_FIELDS:
            raise ValueError("page provenance range fields are invalid")
        start = item["byte_start"]
        end = item["byte_end"]
        page = item["page_number"]
        candidates = item["candidate_pages"]
        method = item["method"]
        source = item["source"]
        operation = item["operation"]
        if (
            type(start) is not int
            or type(end) is not int
            or start != cursor
            or not start < end <= len(merged_markdown)
        ):
            raise ValueError("page provenance ranges do not form an exact partition")
        if (
            type(page) is not int
            or not 1 <= page <= page_count
            or not isinstance(candidates, list)
            or candidates != list(dict.fromkeys(candidates))
            or page not in candidates
            or any(
                type(candidate) is not int or not 1 <= candidate <= page_count
                for candidate in candidates
            )
            or method not in _METHODS
            or not isinstance(source, str)
            or not source
            or (operation is not None and not isinstance(operation, str))
            or not _is_sha256(item["evidence_digest"])
            or not _is_sha256(item["range_id"])
        ):
            raise ValueError("page provenance range is invalid")

        size = end - start
        validated_ranges.append(_PageRange(start, end, page))
        method_counts[method] += 1
        method_bytes[method] += size
        source_counts[source] += 1
        source_bytes[source] += size
        cursor = end

    if cursor != len(merged_markdown):
        raise ValueError("page provenance ranges do not form an exact partition")

    expected_summary = {
        "range_counts_by_method": dict(sorted(method_counts.items())),
        "byte_counts_by_method": dict(sorted(method_bytes.items())),
        "range_counts_by_source": dict(sorted(source_counts.items())),
        "byte_counts_by_source": dict(sorted(source_bytes.items())),
    }
    if payload["summary"] != expected_summary:
        raise ValueError("page provenance summary is invalid")
    if not isinstance(payload["llm_receipts"], list):
        raise ValueError("page provenance LLM receipts are invalid")

    core = {key: value for key, value in payload.items() if key != "record_sha256"}
    if payload["record_sha256"] != _sha256(_canonical_json(core)):
        raise ValueError("page provenance record digest is invalid")

    range_tuple = tuple(validated_ranges)
    return MergedPageProvenance(
        ranges=range_tuple,
        range_starts=tuple(item.byte_start for item in range_tuple),
        markdown_size_bytes=len(merged_markdown),
        schema=payload["schema"],
        contract_version=payload["contract_version"],
        record_sha256=payload["record_sha256"],
        expected_page_count=page_count,
        summary=expected_summary,
    )
