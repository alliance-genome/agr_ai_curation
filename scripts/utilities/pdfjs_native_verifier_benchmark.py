#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from src.lib.pdf_viewer.rapidfuzz_matcher import (  # noqa: E402
    PdfPageText,
    match_quote_to_pdf_pages,
)


DEFAULT_NODE_PROBE = REPO_ROOT / "scripts" / "utilities" / "pdfjs_find_probe.mjs"
DEFAULT_PAGE_CORPUS = Path("/tmp/pdf-page-corpus.json")
DEFAULT_EXPECTED_THRESHOLDS = (0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95)
DEFAULT_NATIVE_THRESHOLDS = (0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80, 0.90)
CURRENT_EXPECTED_THRESHOLD = 0.90
CURRENT_NATIVE_THRESHOLD = 0.60
DEFAULT_LABEL_POSITIVE_F1 = 0.90
DEFAULT_LABEL_NEGATIVE_F1 = 0.75


@dataclass
class ReferenceSpan:
    page_number: int
    start: int
    end: int
    source: str
    text: str


@dataclass
class MatchCandidate:
    method: str
    page_number: int
    start: int
    end: int
    score: float
    distance: float | None
    matched_text: str


def parse_thresholds(value: str, *, label: str) -> tuple[float, ...]:
    thresholds: list[float] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        threshold = float(part)
        if threshold < 0 or threshold > 1:
            raise argparse.ArgumentTypeError(f"{label} thresholds must be between 0 and 1: {part}")
        thresholds.append(threshold)
    if not thresholds:
        raise argparse.ArgumentTypeError(f"No valid {label} thresholds were provided")
    return tuple(sorted(set(thresholds)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the PDF.js native-highlight verifier using the real backend RapidFuzz "
            "runtime matcher plus the existing 100-quote benchmark corpus."
        ),
    )
    parser.add_argument(
        "--benchmark-report",
        required=True,
        help="Path to a JSON report produced by pdfjs_quote_benchmark.mjs",
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the source PDF used by the benchmark",
    )
    parser.add_argument(
        "--page-corpus",
        default=str(DEFAULT_PAGE_CORPUS),
        help="Path to cached PDF.js page corpus JSON. Generated automatically if missing.",
    )
    parser.add_argument(
        "--node-probe",
        default=str(DEFAULT_NODE_PROBE),
        help="Path to pdfjs_find_probe.mjs",
    )
    parser.add_argument(
        "--output",
        help="Write the full JSON benchmark report to this path",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=70.0,
        help="Minimum RapidFuzz score passed to the backend runtime matcher (default: 70.0)",
    )
    parser.add_argument(
        "--candidate-reference-f1-min",
        type=float,
        default=0.95,
        help=(
            "Only quotes whose RapidFuzz localization has at least this span F1 against the silver "
            "reference are used for threshold tuning (default: 0.95)"
        ),
    )
    parser.add_argument(
        "--label-positive-f1",
        type=float,
        default=DEFAULT_LABEL_POSITIVE_F1,
        help=(
            "Selected PDF.js occurrences with at least this span F1 against the silver reference "
            f"are labeled positive (default: {DEFAULT_LABEL_POSITIVE_F1:.2f})"
        ),
    )
    parser.add_argument(
        "--label-negative-f1",
        type=float,
        default=DEFAULT_LABEL_NEGATIVE_F1,
        help=(
            "Selected PDF.js occurrences with span F1 below this threshold are labeled negative. "
            f"Mid-range cases are treated as ambiguous (default: {DEFAULT_LABEL_NEGATIVE_F1:.2f})"
        ),
    )
    parser.add_argument(
        "--expected-thresholds",
        type=lambda value: parse_thresholds(value, label="expected"),
        default=DEFAULT_EXPECTED_THRESHOLDS,
        help=(
            "Comma-separated expected-coverage thresholds to sweep "
            f"(default: {','.join(f'{value:.2f}' for value in DEFAULT_EXPECTED_THRESHOLDS)})"
        ),
    )
    parser.add_argument(
        "--native-thresholds",
        type=lambda value: parse_thresholds(value, label="native"),
        default=DEFAULT_NATIVE_THRESHOLDS,
        help=(
            "Comma-separated native-coverage thresholds to sweep "
            f"(default: {','.join(f'{value:.2f}' for value in DEFAULT_NATIVE_THRESHOLDS)})"
        ),
    )
    return parser.parse_args()


def make_page_texts(page_corpus_report: dict[str, Any]) -> dict[int, str]:
    return {
        int(page_record["pageNumber"]): page_record["pdfjsSearchText"]
        for page_record in page_corpus_report["pages"]
    }


def build_pdf_pages(page_texts: dict[int, str]) -> list[PdfPageText]:
    return [
        PdfPageText(page_number=page_number, raw_text=page_text)
        for page_number, page_text in sorted(page_texts.items())
    ]


def build_match_candidate(method: str, matched_page: int | None, matched_range: Any, matched_query: str | None, score: float) -> MatchCandidate | None:
    if matched_page is None or matched_range is None or matched_query is None:
        return None
    return MatchCandidate(
        method=method,
        page_number=int(matched_page),
        start=int(matched_range.raw_start),
        end=int(matched_range.raw_end_exclusive),
        score=float(score),
        distance=None,
        matched_text=matched_query,
    )


def build_probe_query_specs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for record in records:
        candidate = record["candidate"]
        if candidate is None:
            continue
        specs.append(
            {
                "id": str(record["benchmark_index"]),
                "query": candidate["matched_text"],
                "preferredPageNumber": candidate["page_number"],
            }
        )
    return specs


def ensure_page_corpus(pdf_path: Path, page_corpus_path: Path, node_probe_path: Path) -> dict[str, Any]:
    if page_corpus_path.exists():
        return json.loads(page_corpus_path.read_text("utf8"))

    page_corpus_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "node",
        str(node_probe_path),
        "--pdf",
        str(pdf_path),
        "--output",
        str(page_corpus_path),
    ]
    subprocess.run(command, check=True, cwd=REPO_ROOT)
    return json.loads(page_corpus_path.read_text("utf8"))


def run_pdfjs_probe(
    *,
    pdf_path: Path,
    node_probe_path: Path,
    query_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pdfjs-native-verifier-") as temp_dir:
        temp_dir_path = Path(temp_dir)
        query_file = temp_dir_path / "queries.json"
        output_file = temp_dir_path / "probe.json"
        query_file.write_text(json.dumps(query_specs, indent=2) + "\n", encoding="utf8")
        command = [
            "node",
            str(node_probe_path),
            "--pdf",
            str(pdf_path),
            "--query-file",
            str(query_file),
            "--output",
            str(output_file),
        ]
        subprocess.run(command, check=True, cwd=REPO_ROOT)
        return json.loads(output_file.read_text("utf8"))


def build_selected_occurrence_candidate(probe_result: dict[str, Any]) -> MatchCandidate | None:
    occurrence = probe_result.get("selectedOccurrence")
    if occurrence is None:
        return None
    raw_start = occurrence.get("rawStart")
    raw_end = occurrence.get("rawEndExclusive")
    page_number = occurrence.get("pageNumber")
    if raw_start is None or raw_end is None or page_number is None:
        return None
    return MatchCandidate(
        method="pdfjs_selected_occurrence",
        page_number=int(page_number),
        start=int(raw_start),
        end=int(raw_end),
        score=float(probe_result.get("matchesTotal") or 0),
        distance=None,
        matched_text=occurrence.get("rawSlice") or "",
    )


def locate_reference_span(page_text: str, needle: str) -> tuple[int, int] | None:
    if not needle:
        return None
    index = page_text.find(needle)
    if index >= 0:
        return index, index + len(needle)
    stripped = needle.strip()
    if stripped and stripped != needle:
        index = page_text.find(stripped)
        if index >= 0:
            return index, index + len(stripped)
    return None


def build_reference_span(result: dict[str, Any], page_texts: dict[int, str]) -> ReferenceSpan | None:
    raw_probe = result["probeVariants"]["raw"]
    literal_matches = raw_probe.get("literalPdfjsNormalizedQueryMatches") or []
    if literal_matches:
        page_number = int(literal_matches[0]["pageNumber"])
        start = int(literal_matches[0]["indices"][0])
        query_text = raw_probe.get("pdfjsNormalizedQuery") or ""
        if not query_text:
            return None
        end = start + len(query_text)
        return ReferenceSpan(
            page_number=page_number,
            start=start,
            end=end,
            source="literal_pdfjs_match",
            text=page_texts[page_number][start:end],
        )

    whitespace_matches = raw_probe.get("whitespaceCollapsedMatches") or []
    if whitespace_matches:
        first_occurrence = whitespace_matches[0].get("occurrences", [None])[0]
        if first_occurrence:
            page_number = int(first_occurrence["pageNumber"])
            page_text = page_texts[page_number]
            raw_slice = first_occurrence.get("rawSlice") or ""
            span = locate_reference_span(page_text, raw_slice)
            if span:
                start, end = span
                return ReferenceSpan(
                    page_number=page_number,
                    start=start,
                    end=end,
                    source="whitespace_collapsed_occurrence",
                    text=page_text[start:end],
                )

    failure_analysis = (result.get("diagnostics") or {}).get("failureAnalysis") or {}
    nearest_candidates = failure_analysis.get("nearestCandidates") or []
    if nearest_candidates:
        first_candidate = nearest_candidates[0]
        page_number = int(first_candidate["pageNumber"])
        page_text = page_texts[page_number]
        raw_slice = first_candidate.get("rawSlice") or ""
        span = locate_reference_span(page_text, raw_slice)
        if span:
            start, end = span
            return ReferenceSpan(
                page_number=page_number,
                start=start,
                end=end,
                source="diagnostic_nearest_candidate",
                text=page_text[start:end],
            )

    return None


def compute_overlap_metrics(reference: ReferenceSpan | None, candidate: MatchCandidate | None) -> dict[str, Any]:
    if reference is None:
        return {
            "has_reference": False,
            "page_match": None,
            "span_overlap_chars": None,
            "reference_coverage": None,
            "candidate_coverage": None,
        }

    if candidate is None:
        return {
            "has_reference": True,
            "page_match": False,
            "span_overlap_chars": 0,
            "reference_coverage": 0.0,
            "candidate_coverage": 0.0,
        }

    if reference.page_number != candidate.page_number:
        return {
            "has_reference": True,
            "page_match": False,
            "span_overlap_chars": 0,
            "reference_coverage": 0.0,
            "candidate_coverage": 0.0,
        }

    overlap_start = max(reference.start, candidate.start)
    overlap_end = min(reference.end, candidate.end)
    overlap = max(0, overlap_end - overlap_start)
    reference_length = max(1, reference.end - reference.start)
    candidate_length = max(1, candidate.end - candidate.start)
    return {
        "has_reference": True,
        "page_match": True,
        "span_overlap_chars": overlap,
        "reference_coverage": overlap / reference_length,
        "candidate_coverage": overlap / candidate_length,
    }


def compute_span_f1(reference_coverage: float | None, candidate_coverage: float | None) -> float | None:
    if reference_coverage is None or candidate_coverage is None:
        return None
    if reference_coverage <= 0 or candidate_coverage <= 0:
        return 0.0
    return (2.0 * reference_coverage * candidate_coverage) / (reference_coverage + candidate_coverage)


def build_label(
    *,
    candidate_reference_f1: float | None,
    selected_reference_f1: float | None,
    candidate_reference_f1_min: float,
    label_positive_f1: float,
    label_negative_f1: float,
) -> str:
    if candidate_reference_f1 is None or candidate_reference_f1 < candidate_reference_f1_min:
        return "excluded"
    if selected_reference_f1 is None:
        return "negative"
    if selected_reference_f1 >= label_positive_f1:
        return "positive"
    if selected_reference_f1 < label_negative_f1:
        return "negative"
    return "ambiguous"


def summarize_metric(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
        }
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def accepts_threshold(record: dict[str, Any], expected_threshold: float, native_threshold: float) -> bool:
    metrics = record["native_metrics"]
    if not metrics["page_match"]:
        return False
    expected_coverage = metrics["expected_coverage"] or 0.0
    native_coverage = metrics["native_coverage"] or 0.0
    return expected_coverage >= expected_threshold and native_coverage >= native_threshold


def threshold_sweep(
    records: list[dict[str, Any]],
    expected_thresholds: tuple[float, ...],
    native_thresholds: tuple[float, ...],
) -> list[dict[str, Any]]:
    labeled_records = [record for record in records if record["label"] in {"positive", "negative"}]
    sweep: list[dict[str, Any]] = []

    for expected_threshold in expected_thresholds:
        for native_threshold in native_thresholds:
            true_positive = 0
            false_positive = 0
            true_negative = 0
            false_negative = 0

            for record in labeled_records:
                accepted = accepts_threshold(record, expected_threshold, native_threshold)
                if record["label"] == "positive":
                    if accepted:
                        true_positive += 1
                    else:
                        false_negative += 1
                else:
                    if accepted:
                        false_positive += 1
                    else:
                        true_negative += 1

            total = true_positive + false_positive + true_negative + false_negative
            precision = (
                true_positive / (true_positive + false_positive)
                if (true_positive + false_positive) > 0
                else None
            )
            recall = (
                true_positive / (true_positive + false_negative)
                if (true_positive + false_negative) > 0
                else None
            )
            f1 = (
                (2 * precision * recall) / (precision + recall)
                if precision is not None and recall is not None and (precision + recall) > 0
                else None
            )
            accuracy = ((true_positive + true_negative) / total) if total > 0 else None

            sweep.append(
                {
                    "expected_threshold": expected_threshold,
                    "native_threshold": native_threshold,
                    "true_positive": true_positive,
                    "false_positive": false_positive,
                    "true_negative": true_negative,
                    "false_negative": false_negative,
                    "precision": precision,
                    "recall": recall,
                    "f1": f1,
                    "accuracy": accuracy,
                    "evaluated_case_count": total,
                }
            )

    sweep.sort(
        key=lambda entry: (
            entry["f1"] if entry["f1"] is not None else -1.0,
            entry["accuracy"] if entry["accuracy"] is not None else -1.0,
            -entry["expected_threshold"],
            -entry["native_threshold"],
        ),
        reverse=True,
    )
    return sweep


def select_examples(
    records: list[dict[str, Any]],
    *,
    expected_threshold: float,
    native_threshold: float,
) -> dict[str, list[dict[str, Any]]]:
    false_rejects = []
    false_accepts = []

    for record in records:
        accepted = accepts_threshold(record, expected_threshold, native_threshold)
        if record["label"] == "positive" and not accepted:
            false_rejects.append(record)
        elif record["label"] == "negative" and accepted:
            false_accepts.append(record)

    def sort_key(record: dict[str, Any]) -> tuple[float, float]:
        selected_reference_f1 = record["selected_reference_span_f1"]
        expected_coverage = record["native_metrics"]["expected_coverage"] or 0.0
        return (
            selected_reference_f1 if selected_reference_f1 is not None else -1.0,
            expected_coverage,
        )

    false_rejects.sort(key=sort_key, reverse=True)
    false_accepts.sort(key=sort_key, reverse=True)

    return {
        "false_reject_examples": false_rejects[:5],
        "false_accept_examples": false_accepts[:5],
    }


def main() -> int:
    args = parse_args()
    benchmark_path = Path(args.benchmark_report).resolve()
    pdf_path = Path(args.pdf).resolve()
    page_corpus_path = Path(args.page_corpus).resolve()
    node_probe_path = Path(args.node_probe).resolve()

    benchmark_report = json.loads(benchmark_path.read_text("utf8"))
    page_corpus_report = ensure_page_corpus(pdf_path, page_corpus_path, node_probe_path)
    page_texts = make_page_texts(page_corpus_report)
    pdf_pages = build_pdf_pages(page_texts)

    records: list[dict[str, Any]] = []
    for quote_result in benchmark_report["benchmark"]["results"]:
        query = quote_result["quote"]["claimedQuote"]
        reference = build_reference_span(quote_result, page_texts)
        page_hint = quote_result["quote"].get("pageNumber")
        page_hints = [int(page_hint)] if isinstance(page_hint, int) and page_hint >= 1 else []

        started = time.perf_counter()
        runtime_match = match_quote_to_pdf_pages(
            query,
            pdf_pages,
            page_hints=page_hints,
            min_score=args.min_score,
        )
        duration_ms = (time.perf_counter() - started) * 1000.0

        candidate = build_match_candidate(
            "rapidfuzz_backend_runtime",
            runtime_match.matched_page,
            runtime_match.matched_range,
            runtime_match.matched_query,
            runtime_match.score,
        )
        reference_metrics = compute_overlap_metrics(reference, candidate)
        reference_span_f1 = compute_span_f1(
            reference_metrics["reference_coverage"],
            reference_metrics["candidate_coverage"],
        )

        records.append(
            {
                "benchmark_index": int(quote_result["benchmarkIndex"]),
                "quote": query,
                "section": quote_result["quote"]["topLevelSection"],
                "page_hints": page_hints,
                "reference": None if reference is None else {
                    "page_number": reference.page_number,
                    "start": reference.start,
                    "end": reference.end,
                    "source": reference.source,
                    "text": reference.text,
                },
                "candidate": None if candidate is None else {
                    "page_number": candidate.page_number,
                    "start": candidate.start,
                    "end": candidate.end,
                    "score": candidate.score,
                    "matched_text": candidate.matched_text,
                },
                "runtime_match": {
                    "found": runtime_match.found,
                    "strategy": runtime_match.strategy,
                    "score": runtime_match.score,
                    "matched_page": runtime_match.matched_page,
                    "cross_page": runtime_match.cross_page,
                    "page_ranges": [
                        {
                            "page_number": page_range.page_number,
                            "raw_start": page_range.raw_start,
                            "raw_end_exclusive": page_range.raw_end_exclusive,
                            "query": page_range.query,
                        }
                        for page_range in runtime_match.page_ranges
                    ],
                    "note": runtime_match.note,
                },
                "candidate_reference_metrics": reference_metrics,
                "candidate_reference_span_f1": reference_span_f1,
                "duration_ms": duration_ms,
                "original_diagnostics": quote_result["diagnostics"],
            }
        )

    probe_specs = build_probe_query_specs(records)
    probe_report = run_pdfjs_probe(
        pdf_path=pdf_path,
        node_probe_path=node_probe_path,
        query_specs=probe_specs,
    )
    probe_results_by_id = {
        str(query_result.get("queryId")): query_result
        for query_result in probe_report["queries"]
        if query_result.get("queryId") is not None
    }

    for record in records:
        probe_result = probe_results_by_id.get(str(record["benchmark_index"]))
        selected_occurrence = None if probe_result is None else build_selected_occurrence_candidate(probe_result)
        candidate_payload = record["candidate"]
        candidate_for_native = (
            None
            if candidate_payload is None
            else MatchCandidate(
                method="rapidfuzz_backend_runtime",
                page_number=int(candidate_payload["page_number"]),
                start=int(candidate_payload["start"]),
                end=int(candidate_payload["end"]),
                score=float(candidate_payload["score"]),
                distance=None,
                matched_text=str(candidate_payload["matched_text"]),
            )
        )
        native_metrics = compute_overlap_metrics(
            None
            if candidate_for_native is None
            else ReferenceSpan(
                page_number=candidate_for_native.page_number,
                start=candidate_for_native.start,
                end=candidate_for_native.end,
                source="rapidfuzz_backend_runtime",
                text=candidate_for_native.matched_text,
            ),
            selected_occurrence,
        )
        selected_reference_metrics = compute_overlap_metrics(
            None
            if record["reference"] is None
            else ReferenceSpan(
                page_number=int(record["reference"]["page_number"]),
                start=int(record["reference"]["start"]),
                end=int(record["reference"]["end"]),
                source=str(record["reference"]["source"]),
                text=str(record["reference"]["text"]),
            ),
            selected_occurrence,
        )
        selected_reference_span_f1 = compute_span_f1(
            selected_reference_metrics["reference_coverage"],
            selected_reference_metrics["candidate_coverage"],
        )
        label = build_label(
            candidate_reference_f1=record["candidate_reference_span_f1"],
            selected_reference_f1=selected_reference_span_f1,
            candidate_reference_f1_min=args.candidate_reference_f1_min,
            label_positive_f1=args.label_positive_f1,
            label_negative_f1=args.label_negative_f1,
        )

        record["probe"] = None if probe_result is None else {
            "query": probe_result.get("query"),
            "preferred_page_number": probe_result.get("preferredPageNumber"),
            "selected": probe_result.get("selected"),
            "selected_occurrence": probe_result.get("selectedOccurrence"),
            "matches_total": probe_result.get("matchesTotal"),
            "matched_page_count": probe_result.get("matchedPageCount"),
            "final_state": probe_result.get("finalState"),
        }
        record["native_metrics"] = {
            "page_match": native_metrics["page_match"],
            "span_overlap_chars": native_metrics["span_overlap_chars"],
            "expected_coverage": native_metrics["reference_coverage"],
            "native_coverage": native_metrics["candidate_coverage"],
            "exact_range_match": bool(
                candidate_for_native is not None
                and selected_occurrence is not None
                and candidate_for_native.page_number == selected_occurrence.page_number
                and candidate_for_native.start == selected_occurrence.start
                and candidate_for_native.end == selected_occurrence.end
            ),
        }
        record["selected_reference_metrics"] = selected_reference_metrics
        record["selected_reference_span_f1"] = selected_reference_span_f1
        record["label"] = label

    trusted_records = [
        record
        for record in records
        if record["candidate_reference_span_f1"] is not None
        and record["candidate_reference_span_f1"] >= args.candidate_reference_f1_min
    ]
    labeled_records = [record for record in records if record["label"] in {"positive", "negative"}]
    positive_records = [record for record in records if record["label"] == "positive"]
    negative_records = [record for record in records if record["label"] == "negative"]
    ambiguous_records = [record for record in records if record["label"] == "ambiguous"]

    positive_expected_coverages = [
        record["native_metrics"]["expected_coverage"]
        for record in positive_records
        if record["native_metrics"]["expected_coverage"] is not None
    ]
    positive_native_coverages = [
        record["native_metrics"]["native_coverage"]
        for record in positive_records
        if record["native_metrics"]["native_coverage"] is not None
    ]
    negative_expected_coverages = [
        record["native_metrics"]["expected_coverage"]
        for record in negative_records
        if record["native_metrics"]["expected_coverage"] is not None
    ]
    negative_native_coverages = [
        record["native_metrics"]["native_coverage"]
        for record in negative_records
        if record["native_metrics"]["native_coverage"] is not None
    ]

    sweep = threshold_sweep(records, args.expected_thresholds, args.native_thresholds)
    current_threshold_entry = next(
        (
            entry
            for entry in sweep
            if entry["expected_threshold"] == CURRENT_EXPECTED_THRESHOLD
            and entry["native_threshold"] == CURRENT_NATIVE_THRESHOLD
        ),
        None,
    )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "benchmark_report": str(benchmark_path),
        "page_corpus": str(page_corpus_path),
        "pdf_path": str(pdf_path),
        "configuration": {
            "min_score": args.min_score,
            "candidate_reference_f1_min": args.candidate_reference_f1_min,
            "label_positive_f1": args.label_positive_f1,
            "label_negative_f1": args.label_negative_f1,
            "expected_thresholds": list(args.expected_thresholds),
            "native_thresholds": list(args.native_thresholds),
            "current_expected_threshold": CURRENT_EXPECTED_THRESHOLD,
            "current_native_threshold": CURRENT_NATIVE_THRESHOLD,
        },
        "summary": {
            "quote_count": len(records),
            "runtime_match_found_count": sum(1 for record in records if record["runtime_match"]["found"]),
            "probe_selected_occurrence_count": sum(
                1
                for record in records
                if record["probe"] is not None and record["probe"]["selected_occurrence"] is not None
            ),
            "trusted_candidate_count": len(trusted_records),
            "labeled_case_count": len(labeled_records),
            "positive_case_count": len(positive_records),
            "negative_case_count": len(negative_records),
            "ambiguous_case_count": len(ambiguous_records),
            "exact_range_match_count": sum(
                1 for record in records if record["native_metrics"]["exact_range_match"]
            ),
            "candidate_reference_span_f1": summarize_metric(
                [
                    record["candidate_reference_span_f1"]
                    for record in records
                    if record["candidate_reference_span_f1"] is not None
                ]
            ),
            "selected_reference_span_f1": summarize_metric(
                [
                    record["selected_reference_span_f1"]
                    for record in records
                    if record["selected_reference_span_f1"] is not None
                ]
            ),
            "positive_expected_coverage": summarize_metric(positive_expected_coverages),
            "positive_native_coverage": summarize_metric(positive_native_coverages),
            "negative_expected_coverage": summarize_metric(negative_expected_coverages),
            "negative_native_coverage": summarize_metric(negative_native_coverages),
        },
        "threshold_sweep": sweep,
        "current_threshold_result": current_threshold_entry,
        "examples": select_examples(
            records,
            expected_threshold=CURRENT_EXPECTED_THRESHOLD,
            native_threshold=CURRENT_NATIVE_THRESHOLD,
        ),
        "records": records,
    }

    output_text = json.dumps(report, indent=2) + "\n"
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text, encoding="utf8")
        print(f"Wrote PDF.js native verifier benchmark report to {output_path}", file=sys.stderr)
    else:
        sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
