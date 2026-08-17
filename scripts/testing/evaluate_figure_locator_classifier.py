#!/usr/bin/env python3
"""Run the live Terra figure-locator classifier against the labeled corpus."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from src.lib.pipeline.figure_locator_resolution import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    is_figure_locator_candidate,
    resolve_figure_locators,
)
from src.models.chunk import ChunkMetadata, DocumentChunk, ElementType  # noqa: E402  # pyright: ignore[reportMissingImports]

CORPUS_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "figure_locator"
    / "semantic_cases.json"
)


async def main() -> int:
    cases = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    chunks = [
        DocumentChunk(
            id=f"figure-locator-eval-{case['id']}",
            document_id="figure-locator-eval",
            chunk_index=index,
            content=case["text"],
            element_type=ElementType.NARRATIVE_TEXT,
            metadata=ChunkMetadata(
                character_count=len(case["text"]),
                word_count=len(case["text"].split()),
            ),
        )
        for index, case in enumerate(cases)
    ]
    await resolve_figure_locators(chunks)

    candidate_misses: list[str] = []
    false_singletons: list[str] = []
    false_omissions: list[str] = []
    cardinality_mismatches: list[str] = []
    case_results: list[dict[str, object]] = []
    for case, chunk in zip(cases, chunks, strict=True):
        selected = is_figure_locator_candidate(case["text"])
        if case["expected_candidate"] and not selected:
            candidate_misses.append(case["id"])

        resolution = chunk.metadata.figure_locator_resolution
        actual_singletons = {
            annotation.canonical_reference
            for annotation in (resolution.annotations if resolution else [])
            if annotation.cardinality == "single" and annotation.canonical_reference
        }
        expected_singletons = set(case["expected_canonical_references"])
        actual_cardinalities = (
            [annotation.cardinality for annotation in resolution.annotations]
            if resolution and resolution.status == "resolved"
            else (["uncertain"] if resolution else [])
        )
        expected_cardinalities = (
            []
            if case["expected_cardinality"] == "none"
            else [case["expected_cardinality"]]
        )
        case_results.append(
            {
                "id": case["id"],
                "resolution_status": resolution.status if resolution else None,
                "actual_singletons": sorted(actual_singletons),
                "actual_cardinalities": actual_cardinalities,
                "annotations": [
                    annotation.model_dump()
                    for annotation in (resolution.annotations if resolution else [])
                ],
            }
        )
        if actual_singletons - expected_singletons:
            false_singletons.append(case["id"])
        if expected_singletons - actual_singletons:
            false_omissions.append(case["id"])
        if actual_cardinalities != expected_cardinalities:
            cardinality_mismatches.append(case["id"])

    report = {
        "corpus_cases": len(cases),
        "candidate_misses": candidate_misses,
        "false_singletons": false_singletons,
        "false_omissions": false_omissions,
        "cardinality_mismatches": cardinality_mismatches,
        "case_results": case_results,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if any(
        (
            candidate_misses,
            false_singletons,
            false_omissions,
            cardinality_mismatches,
        )
    ) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
