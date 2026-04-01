#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import edlib
from fuzzysearch import find_near_matches
from rapidfuzz import fuzz


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NODE_PROBE = REPO_ROOT / "scripts" / "utilities" / "pdfjs_find_probe.mjs"
DEFAULT_PAGE_CORPUS = Path("/tmp/pdf-page-corpus.json")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Python fuzzy/local-alignment libraries against PDF.js page text.",
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
        help="Write the full JSON bakeoff report to this path",
    )
    parser.add_argument(
        "--fuzzysearch-max-rate",
        type=float,
        default=0.12,
        help="Max edit-distance rate for fuzzysearch relative to query length",
    )
    parser.add_argument(
        "--edlib-max-rate",
        type=float,
        default=0.2,
        help="Max edit-distance rate for edlib relative to query length. Use <=0 for unlimited.",
    )
    return parser.parse_args()


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
        query_text = raw_probe.get("pdfjsNormalizedQuery") or raw_probe.get("normalizedQuery") or raw_probe.get("query") or ""
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


def exact_match(query: str, page_texts: dict[int, str]) -> MatchCandidate | None:
    best: MatchCandidate | None = None
    for page_number, page_text in page_texts.items():
        start = page_text.find(query)
        if start < 0:
            continue
        candidate = MatchCandidate(
            method="exact",
            page_number=page_number,
            start=start,
            end=start + len(query),
            score=100.0,
            distance=0.0,
            matched_text=page_text[start:start + len(query)],
        )
        if best is None or candidate.page_number < best.page_number:
            best = candidate
    return best


def rapidfuzz_match(query: str, page_texts: dict[int, str]) -> MatchCandidate:
    best: MatchCandidate | None = None
    for page_number, page_text in page_texts.items():
        alignment = fuzz.partial_ratio_alignment(query, page_text)
        start = int(alignment.dest_start)
        end = int(alignment.dest_end)
        candidate = MatchCandidate(
            method="rapidfuzz_partial_ratio_alignment",
            page_number=page_number,
            start=start,
            end=end,
            score=float(alignment.score),
            distance=None,
            matched_text=page_text[start:end],
        )
        if best is None or candidate.score > best.score:
            best = candidate
    assert best is not None
    return best


def compute_allowed_distance(query: str, rate: float, floor: int = 4, ceiling: int = 64) -> int:
    return max(floor, min(ceiling, int(math.ceil(len(query) * rate))))


def fuzzysearch_match(query: str, page_texts: dict[int, str], max_rate: float) -> MatchCandidate | None:
    best: MatchCandidate | None = None
    max_l_dist = compute_allowed_distance(query, max_rate)
    for page_number, page_text in page_texts.items():
        matches = find_near_matches(query, page_text, max_l_dist=max_l_dist)
        if not matches:
            continue
        match = min(matches, key=lambda entry: (entry.dist, -(entry.end - entry.start), entry.start))
        score = max(0.0, 100.0 * (1.0 - (match.dist / max(len(query), 1))))
        candidate = MatchCandidate(
            method="fuzzysearch",
            page_number=page_number,
            start=int(match.start),
            end=int(match.end),
            score=score,
            distance=float(match.dist),
            matched_text=page_text[match.start:match.end],
        )
        if best is None or candidate.score > best.score or (
            candidate.score == best.score and (candidate.distance or 0) < (best.distance or 0)
        ):
            best = candidate
    return best


def edlib_match(query: str, page_texts: dict[int, str], max_rate: float) -> MatchCandidate | None:
    best: MatchCandidate | None = None
    k_value = -1 if max_rate <= 0 else compute_allowed_distance(query, max_rate)
    for page_number, page_text in page_texts.items():
        result = edlib.align(query, page_text, mode="HW", task="locations", k=k_value)
        edit_distance = result.get("editDistance", -1)
        locations = result.get("locations") or []
        if edit_distance < 0 or not locations:
            continue
        start, end_inclusive = locations[0]
        end = int(end_inclusive) + 1
        score = max(0.0, 100.0 * (1.0 - (edit_distance / max(len(query), 1))))
        candidate = MatchCandidate(
            method="edlib_hw",
            page_number=page_number,
            start=int(start),
            end=end,
            score=score,
            distance=float(edit_distance),
            matched_text=page_text[start:end],
        )
        if best is None or candidate.score > best.score or (
            candidate.score == best.score and (candidate.distance or 0) < (best.distance or 0)
        ):
            best = candidate
    return best


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


def summarize_method(name: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    with_reference = [entry for entry in results if entry["metrics"]["has_reference"]]
    page_matches = [entry for entry in with_reference if entry["metrics"]["page_match"]]
    ref_coverages = [entry["metrics"]["reference_coverage"] for entry in page_matches]
    candidate_coverages = [entry["metrics"]["candidate_coverage"] for entry in page_matches]
    span_f1_scores = [
        compute_span_f1(entry["metrics"]["reference_coverage"], entry["metrics"]["candidate_coverage"])
        for entry in page_matches
    ]
    span_f1_scores = [score for score in span_f1_scores if score is not None]
    exact_span_matches = [
        entry for entry in page_matches
        if entry["reference"] is not None
        and entry["candidate"] is not None
        and entry["reference"]["start"] == entry["candidate"]["start"]
        and entry["reference"]["end"] == entry["candidate"]["end"]
    ]
    full_reference_coverage = [
        entry for entry in page_matches
        if (entry["metrics"]["reference_coverage"] or 0.0) >= 1.0
    ]
    reference_coverage_95 = [
        entry for entry in page_matches
        if (entry["metrics"]["reference_coverage"] or 0.0) >= 0.95
    ]
    tight_coverage_99 = [
        entry for entry in page_matches
        if (entry["metrics"]["reference_coverage"] or 0.0) >= 0.99
        and (entry["metrics"]["candidate_coverage"] or 0.0) >= 0.99
    ]
    tight_coverage_95 = [
        entry for entry in page_matches
        if (entry["metrics"]["reference_coverage"] or 0.0) >= 0.95
        and (entry["metrics"]["candidate_coverage"] or 0.0) >= 0.95
    ]
    low_reference_coverage = [
        entry for entry in page_matches
        if (entry["metrics"]["reference_coverage"] or 0.0) < 0.90
    ]
    low_candidate_coverage = [
        entry for entry in page_matches
        if (entry["metrics"]["candidate_coverage"] or 0.0) < 0.90
    ]
    exact_page_matches = [
        entry for entry in with_reference
        if entry["reference"]["source"] == "literal_pdfjs_match" and entry["metrics"]["page_match"]
    ]
    fuzzy_page_matches = [
        entry for entry in with_reference
        if entry["reference"]["source"] != "literal_pdfjs_match" and entry["metrics"]["page_match"]
    ]
    durations = [entry["duration_ms"] for entry in results]
    scores = [entry["candidate"]["score"] for entry in results if entry["candidate"] is not None]
    return {
        "method": name,
        "quote_count": len(results),
        "reference_count": len(with_reference),
        "match_count": sum(1 for entry in results if entry["candidate"] is not None),
        "page_match_count": len(page_matches),
        "page_match_rate": (len(page_matches) / len(with_reference)) if with_reference else None,
        "reference_coverage_mean": statistics.fmean(ref_coverages) if ref_coverages else None,
        "reference_coverage_median": statistics.median(ref_coverages) if ref_coverages else None,
        "candidate_coverage_mean": statistics.fmean(candidate_coverages) if candidate_coverages else None,
        "candidate_coverage_median": statistics.median(candidate_coverages) if candidate_coverages else None,
        "span_f1_mean": statistics.fmean(span_f1_scores) if span_f1_scores else None,
        "span_f1_median": statistics.median(span_f1_scores) if span_f1_scores else None,
        "exact_span_match_count": len(exact_span_matches),
        "exact_span_match_rate": (len(exact_span_matches) / len(with_reference)) if with_reference else None,
        "full_reference_coverage_count": len(full_reference_coverage),
        "full_reference_coverage_rate": (len(full_reference_coverage) / len(with_reference)) if with_reference else None,
        "reference_coverage_95_count": len(reference_coverage_95),
        "reference_coverage_95_rate": (len(reference_coverage_95) / len(with_reference)) if with_reference else None,
        "tight_coverage_99_count": len(tight_coverage_99),
        "tight_coverage_99_rate": (len(tight_coverage_99) / len(with_reference)) if with_reference else None,
        "tight_coverage_95_count": len(tight_coverage_95),
        "tight_coverage_95_rate": (len(tight_coverage_95) / len(with_reference)) if with_reference else None,
        "low_reference_coverage_count": len(low_reference_coverage),
        "low_candidate_coverage_count": len(low_candidate_coverage),
        "literal_reference_page_match_count": len(exact_page_matches),
        "nonliteral_reference_page_match_count": len(fuzzy_page_matches),
        "average_duration_ms": statistics.fmean(durations) if durations else None,
        "median_duration_ms": statistics.median(durations) if durations else None,
        "average_score": statistics.fmean(scores) if scores else None,
    }


def main() -> int:
    args = parse_args()
    benchmark_path = Path(args.benchmark_report).resolve()
    pdf_path = Path(args.pdf).resolve()
    page_corpus_path = Path(args.page_corpus).resolve()
    node_probe_path = Path(args.node_probe).resolve()

    benchmark_report = json.loads(benchmark_path.read_text("utf8"))
    page_corpus_report = ensure_page_corpus(pdf_path, page_corpus_path, node_probe_path)
    page_texts = {
        int(page_record["pageNumber"]): page_record["pdfjsSearchText"]
        for page_record in page_corpus_report["pages"]
    }

    method_functions = {
        "exact": lambda query: exact_match(query, page_texts),
        "rapidfuzz_partial_ratio_alignment": lambda query: rapidfuzz_match(query, page_texts),
        "edlib_hw": lambda query: edlib_match(query, page_texts, args.edlib_max_rate),
        "fuzzysearch": lambda query: fuzzysearch_match(query, page_texts, args.fuzzysearch_max_rate),
    }

    per_method_results: dict[str, list[dict[str, Any]]] = {name: [] for name in method_functions}

    for quote_result in benchmark_report["benchmark"]["results"]:
        query = quote_result["quote"]["claimedQuote"]
        reference = build_reference_span(quote_result, page_texts)
        reference_payload = None if reference is None else {
            "page_number": reference.page_number,
            "start": reference.start,
            "end": reference.end,
            "source": reference.source,
            "text": reference.text,
        }

        for method_name, method_fn in method_functions.items():
            started = time.perf_counter()
            candidate = method_fn(query)
            duration_ms = (time.perf_counter() - started) * 1000.0
            metrics = compute_overlap_metrics(reference, candidate)
            candidate_payload = None if candidate is None else {
                "page_number": candidate.page_number,
                "start": candidate.start,
                "end": candidate.end,
                "score": candidate.score,
                "distance": candidate.distance,
                "matched_text": candidate.matched_text,
            }
            per_method_results[method_name].append({
                "benchmark_index": quote_result["benchmarkIndex"],
                "quote": query,
                "section": quote_result["quote"]["topLevelSection"],
                "reference": reference_payload,
                "candidate": candidate_payload,
                "metrics": metrics,
                "duration_ms": duration_ms,
                "original_diagnostics": quote_result["diagnostics"],
            })

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "benchmark_report": str(benchmark_path),
        "page_corpus": str(page_corpus_path),
        "pdf_path": str(pdf_path),
        "methods": {
            name: {
                "summary": summarize_method(name, results),
                "results": results,
            }
            for name, results in per_method_results.items()
        },
    }

    output_text = json.dumps(report, indent=2) + "\n"
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text, encoding="utf8")
        print(f"Wrote PDF text matcher bakeoff report to {output_path}", file=sys.stderr)
    else:
        sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
