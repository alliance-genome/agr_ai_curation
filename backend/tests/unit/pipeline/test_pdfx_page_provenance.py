"""Contract tests for PDFX merged page-provenance consumption."""

from __future__ import annotations

import hashlib
import json

import pytest

from src.lib.pipeline.pdfx_page_provenance import parse_merged_page_provenance
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sign(payload: dict) -> None:
    core = {key: value for key, value in payload.items() if key != "record_sha256"}
    payload["record_sha256"] = _sha256(_canonical_json(core))


def _page_payload(markdown: bytes, ranges: list[dict], *, page_count: int = 3) -> bytes:
    method_counts: dict[str, int] = {}
    method_bytes: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    source_bytes: dict[str, int] = {}
    for item in ranges:
        size = item["byte_end"] - item["byte_start"]
        method = item["method"]
        source = item["source"]
        method_counts[method] = method_counts.get(method, 0) + 1
        method_bytes[method] = method_bytes.get(method, 0) + size
        source_counts[source] = source_counts.get(source, 0) + 1
        source_bytes[source] = source_bytes.get(source, 0) + size

    payload = {
        "schema": "pdfx-merged-page-provenance",
        "contract_version": "merged-page-provenance-v1",
        "pdf_sha256": _sha256(b"pdf"),
        "merged_markdown_sha256": _sha256(markdown),
        "merged_markdown_size_bytes": len(markdown),
        "audit_sha256": _sha256(b"audit"),
        "merge_contract_id": "pdfx-native-skeleton-primary-page-v1",
        "source_map_sha256": {"grobid": _sha256(b"source-map")},
        "expected_page_count": page_count,
        "ranges": ranges,
        "summary": {
            "range_counts_by_method": dict(sorted(method_counts.items())),
            "byte_counts_by_method": dict(sorted(method_bytes.items())),
            "range_counts_by_source": dict(sorted(source_counts.items())),
            "byte_counts_by_source": dict(sorted(source_bytes.items())),
        },
        "llm_receipts": [],
    }
    _sign(payload)
    return _canonical_json(payload) + b"\n"


def _range(start: int, end: int, page: int, *, method: str = "direct") -> dict:
    return {
        "byte_start": start,
        "byte_end": end,
        "page_number": page,
        "candidate_pages": [page],
        "method": method,
        "source": "grobid",
        "operation": None,
        "evidence_digest": _sha256(f"evidence:{start}:{end}".encode()),
        "range_id": _sha256(f"range:{start}:{end}".encode()),
    }


def test_page_index_uses_utf8_byte_offsets_and_exact_merged_binding():
    markdown = "# α title\n\nBody\n".encode("utf-8")
    body_start = markdown.index(b"Body")
    raw = _page_payload(
        markdown,
        [_range(0, body_start, 1), _range(body_start, len(markdown), 3)],
    )

    provenance = parse_merged_page_provenance(raw, merged_markdown=markdown)

    assert provenance.page_for_byte_offset(len("# α title\n\n".encode("utf-8"))) == 3
    assert provenance.page_for_byte_offset(body_start - 1) == 1
    assert provenance.receipt() == {
        "schema": "pdfx-merged-page-provenance",
        "contract_version": "merged-page-provenance-v1",
        "record_sha256": json.loads(raw)["record_sha256"],
        "expected_page_count": 3,
        "range_count": 2,
        "summary": json.loads(raw)["summary"],
    }


def test_markdown_elements_use_sidecar_first_content_page_for_spanning_blocks():
    markdown = "# Results\n\nFirst line\nsecond line\n\n- item\n"
    encoded = markdown.encode("utf-8")
    paragraph_start = encoded.index(b"First")
    second_line_start = encoded.index(b"second")
    list_start = encoded.index(b"- item")
    provenance = parse_merged_page_provenance(
        _page_payload(
            encoded,
            [
                _range(0, paragraph_start, 1),
                _range(paragraph_start, second_line_start, 2),
                _range(second_line_start, list_start, 3),
                _range(list_start, len(encoded), 3),
            ],
        ),
        merged_markdown=encoded,
    )

    elements = markdown_to_pipeline_elements(markdown, page_provenance=provenance)

    assert [element["metadata"]["page_number"] for element in elements] == [1, 2, 3]
    assert elements[1]["text"] == "First line second line"
    assert all("bbox" not in element["metadata"] for element in elements)
    assert all("provenance" not in element["metadata"] for element in elements)


def test_sidecar_pages_are_authoritative_over_legacy_marker_state():
    markdown = "<!-- page: 9 -->\n# Results\n"
    encoded = markdown.encode("utf-8")
    provenance = parse_merged_page_provenance(
        _page_payload(encoded, [_range(0, len(encoded), 2)], page_count=9),
        merged_markdown=encoded,
    )

    elements = markdown_to_pipeline_elements(markdown, page_provenance=provenance)

    assert [element["metadata"]["page_number"] for element in elements] == [2]


def test_tables_and_code_blocks_use_their_first_content_byte_page():
    markdown = "| A | B |\n| - | - |\n\n```text\nvalue\n```\n"
    encoded = markdown.encode("utf-8")
    code_start = encoded.index(b"```text")
    provenance = parse_merged_page_provenance(
        _page_payload(
            encoded,
            [
                _range(0, code_start, 1),
                _range(code_start, len(encoded), 2),
            ],
        ),
        merged_markdown=encoded,
    )

    elements = markdown_to_pipeline_elements(markdown, page_provenance=provenance)

    assert [element["metadata"]["content_type"] for element in elements] == [
        "table",
        "code_block",
    ]
    assert [element["metadata"]["page_number"] for element in elements] == [1, 2]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload.update(merged_markdown_sha256="0" * 64),
            "merged Markdown binding",
        ),
        (
            lambda payload: payload["ranges"][1].update(
                byte_start=payload["ranges"][0]["byte_end"] + 1
            ),
            "partition",
        ),
        (
            lambda payload: payload["ranges"][1].update(
                page_number=99, candidate_pages=[99]
            ),
            "range",
        ),
    ],
)
def test_invalid_sidecar_contract_fails_closed(mutate, message):
    markdown = b"# Title\n\nBody\n"
    split = markdown.index(b"Body")
    payload = json.loads(
        _page_payload(markdown, [_range(0, split, 1), _range(split, len(markdown), 2)])
    )
    mutate(payload)
    _sign(payload)

    with pytest.raises(ValueError, match=message):
        parse_merged_page_provenance(_canonical_json(payload), merged_markdown=markdown)


def test_invalid_record_digest_fails_closed():
    markdown = b"# Title\n"
    payload = json.loads(_page_payload(markdown, [_range(0, len(markdown), 1)]))
    payload["record_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="record digest"):
        parse_merged_page_provenance(_canonical_json(payload), merged_markdown=markdown)
