#!/usr/bin/env python3
"""Run real-PDF domain-envelope corpus trials against a live backend."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

import dev_release_smoke as smoke


DEFAULT_BASE_URL = "http://192.168.86.44:8900"
DEFAULT_OUTPUT_DIR = Path("docs/design/pdf-corpus-trials")
DEFAULT_DOWNLOAD_DIR = Path("/tmp/agr_domain_envelope_pdf_corpus")
BUILDER_REQUIRED_DOMAINS = frozenset(
    {"allele", "gene", "disease", "chemical_condition", "phenotype"}
)


@dataclass(frozen=True)
class CorpusTrial:
    trial_id: str
    domain: str
    agent_ids: tuple[str, ...]
    title: str
    organism: str
    pmcid: str
    pmid: str | None
    doi: str | None
    pdf_url: str
    prompt: str
    expected_validator_bindings: tuple[str, ...]
    minimum_expected_validator_bindings: int | None = None
    agent_prompts: Mapping[str, str] | None = None


class FlowExecutionFailure(smoke.SmokeFailure):
    """Flow execution failed after returning SSE evidence worth preserving."""

    def __init__(self, message: str, *, summary: dict[str, Any]) -> None:
        super().__init__(message)
        self.summary = summary


TRIALS: tuple[CorpusTrial, ...] = (
    CorpusTrial(
        trial_id="gene_drosophila_crb_rhabdomere",
        domain="gene",
        agent_ids=("gene_extractor",),
        title="Crumbs and the apical spectrin cytoskeleton regulate R8 cell fate in the Drosophila eye",
        organism="Drosophila melanogaster",
        pmcid="PMC8211197",
        pmid="34097697",
        doi="10.1371/journal.pgen.1009146",
        pdf_url="https://journals.plos.org/plosgenetics/article/file?id=10.1371/journal.pgen.1009146&type=printable",
        prompt=(
            "Read the loaded paper and extract exactly one paper-grounded gene candidate: "
            "Drosophila crb/Crumbs in the context of rhabdomere or photoreceptor apical-domain "
            "morphogenesis. Use only evidence in the paper, call record_evidence for one exact "
            "supporting quote, include organism/species hints, and do not extract other genes."
        ),
        expected_validator_bindings=("alliance_gene_reference_lookup",),
    ),
    CorpusTrial(
        trial_id="gene_drosophila_r8_tgfb_multi_gene",
        domain="gene",
        agent_ids=("gene_extractor",),
        title=(
            "Parallel Activin and BMP signaling coordinates R7/R8 "
            "photoreceptor subtype pairing in the stochastic Drosophila retina"
        ),
        organism="Drosophila melanogaster",
        pmcid="PMC5599232",
        pmid="28853393",
        doi="10.7554/eLife.25301",
        pdf_url="https://cdn.elifesciences.org/articles/25301/elife-25301-v2.pdf",
        prompt=(
            "Read the loaded paper and extract four distinct paper-grounded "
            "Drosophila gene candidates from the Activin/BMP/Hippo/R8 subtype "
            "specification findings. Prefer concrete genes explicitly discussed "
            "in the abstract or results, such as babo/Baboon, dSmad2, Mad, "
            "wts/Warts, melt/Melted, Rh5, or Rh6. Use record_evidence for one "
            "exact supporting quote per retained gene, include Drosophila "
            "melanogaster, FlyBase/FB, and NCBITaxon:7227 hints when supported, "
            "and do not resolve gene IDs in the extractor."
        ),
        expected_validator_bindings=("alliance_gene_reference_lookup",),
    ),
    CorpusTrial(
        trial_id="allele_drosophila_notch_facet_glossy",
        domain="allele",
        agent_ids=("allele_extractor",),
        title="Notch Controls Cell Adhesion in the Drosophila Eye",
        organism="Drosophila melanogaster",
        pmcid="PMC3886913",
        pmid="24415930",
        doi="10.1371/journal.pgen.1004087",
        pdf_url="https://journals.plos.org/plosgenetics/article/file?id=10.1371/journal.pgen.1004087&type=printable",
        prompt=(
            "Read the loaded paper and extract exactly one allele or variant candidate: "
            "the Drosophila Notch facet-glossy allele, written as Nfa-g, N^{fa-g}, or similar. "
            "Preserve the paper notation, genotype/phenotype context, organism hints, and one "
            "record_evidence verified quote. Do not resolve allele IDs in the extractor."
        ),
        expected_validator_bindings=("allele_mention_reference_validation",),
    ),
    CorpusTrial(
        trial_id="disease_mouse_pkd1_adpkd",
        domain="disease",
        agent_ids=("disease_extractor",),
        title="Network Analysis of a Pkd1-Mouse Model of Autosomal Dominant Polycystic Kidney Disease Identifies HNF4alpha as a Disease Modifier",
        organism="Mus musculus",
        pmcid="PMC3516559",
        pmid="23209420",
        doi="10.1371/journal.pgen.1003053",
        pdf_url="https://journals.plos.org/plosgenetics/article/file?id=10.1371/journal.pgen.1003053&type=printable",
        prompt=(
            "Read the loaded paper and extract exactly one disease assertion candidate: "
            "the Pkd1 mouse model of autosomal dominant polycystic kidney disease. Preserve disease mention, "
            "model organism/subject context, role, and one record_evidence verified quote. "
            "Do not perform disease ontology lookup in the extractor."
        ),
        expected_validator_bindings=(
            "disease_ontology_term_lookup",
            "disease_relation_cv_lookup",
            "disease_data_provider_lookup",
        ),
    ),
    CorpusTrial(
        trial_id="chemical_zebrafish_estradiol_segmentation",
        domain="chemical_condition",
        agent_ids=("chemical_extractor",),
        title="Small molecule screen in embryonic zebrafish using modular variations to target segmentation",
        organism="Danio rerio",
        pmcid="PMC5711842",
        pmid="29196643",
        doi="10.1038/s41467-017-01469-5",
        pdf_url="https://www.nature.com/articles/s41467-017-01469-5.pdf",
        prompt=(
            "Read the loaded paper and extract exactly one chemical or experimental-condition "
            "candidate: estradiol treatment in embryonic zebrafish segmentation experiments. "
            "Preserve dose/timing/context and one record_evidence verified quote. Do not resolve "
            "ChEBI or condition ontology IDs in the extractor."
        ),
        expected_validator_bindings=(
            "chemical_condition.chebi_api_lookup",
            "chemical_condition.term_chebi_api_lookup",
            "chemical_condition.condition_ontology_lookup",
            "chemical_condition.condition_relation_type_lookup",
        ),
    ),
    CorpusTrial(
        trial_id="phenotype_celegans_mus81_reduced_brood",
        domain="phenotype",
        agent_ids=("phenotype_extractor",),
        title="Joint Molecule Resolution Requires the Redundant Activities of MUS-81 and XPF-1 during Caenorhabditis elegans Meiosis",
        organism="Caenorhabditis elegans",
        pmcid="PMC3715453",
        pmid="23874212",
        doi="10.1371/journal.pgen.1003582",
        pdf_url="https://journals.plos.org/plosgenetics/article/file?id=10.1371/journal.pgen.1003582&type=printable",
        prompt=(
            "Read the loaded paper and extract exactly one phenotype assertion candidate: "
            "the reduced brood size phenotype reported for C. elegans mus-81(tm1937) mutants. "
            "Preserve subject, phenotype statement, organism hints, and one record_evidence "
            "verified quote. Do not resolve phenotype ontology IDs in the extractor."
        ),
        expected_validator_bindings=("phenotype_term_ontology_validator",),
    ),
    CorpusTrial(
        trial_id="gene_expression_zebrafish_flcn_brain",
        domain="gene_expression",
        agent_ids=("gene_expression",),
        title="Expression and knockdown of zebrafish folliculin suggests requirement for embryonic brain morphogenesis",
        organism="Danio rerio",
        pmcid="PMC4939010",
        pmid="27391801",
        doi="10.1186/s12861-016-0119-8",
        pdf_url="https://bmcdevbiol.biomedcentral.com/counter/pdf/10.1186/s12861-016-0119-8.pdf",
        prompt=(
            "Read the loaded paper and extract exactly one gene-expression observation candidate: "
            "zebrafish flcn expression during embryonic development, preferably brain, retina, "
            "hatching gland, or fin-bud context if supported. Preserve anatomy/stage/taxon hints "
            "and one record_evidence verified quote. Use the explicit zebrafish organism context "
            "as the ZFIN data-provider selector, but do not resolve anatomy, stage, gene, or "
            "provider database IDs in the extractor."
        ),
        expected_validator_bindings=(
            "relation_vocabulary_validation",
            "data_provider_validation",
        ),
    ),
    CorpusTrial(
        trial_id="cross_domain_zebrafish_segmentation_screen",
        domain="cross_domain",
        agent_ids=("chemical_extractor", "phenotype_extractor", "gene_extractor"),
        title="Small molecule screen in embryonic zebrafish using modular variations to target segmentation",
        organism="Danio rerio",
        pmcid="PMC5711842",
        pmid="29196643",
        doi="10.1038/s41467-017-01469-5",
        pdf_url="https://www.nature.com/articles/s41467-017-01469-5.pdf",
        prompt=(
            "Run a compact cross-domain pass over this zebrafish segmentation paper. Extract "
            "one chemical condition, one phenotype statement, and one central gene supported by "
            "the paper. Each extractor must use record_evidence for one exact supporting quote "
            "and must leave identity/ontology resolution to validators. Preserve the explicit "
            "zebrafish organism context as ZFIN/NCBITaxon:7955 selector hints for gene or "
            "phenotype candidates; those hints are not final identity resolution."
        ),
        expected_validator_bindings=(
            "chemical_condition.chebi_api_lookup",
            "phenotype_term_ontology_validator",
            "alliance_gene_reference_lookup",
        ),
        agent_prompts={
            "chemical_extractor": (
                "Read the loaded paper and extract exactly one chemical or experimental-condition "
                "candidate: SB225002 treatment in embryonic zebrafish segmentation experiments. "
                "Preserve dose/timing/context when present and use record_evidence for one exact "
                "supporting quote. Do not extract phenotype statements or genes in this step, and "
                "do not resolve ChEBI or condition ontology IDs in the extractor."
            ),
            "phenotype_extractor": (
                "Read the loaded paper and extract exactly one phenotype assertion candidate: "
                "mid-trunk myotome boundary or segmentation defects in embryonic zebrafish after "
                "SB225002 treatment. Preserve the Danio rerio/ZFIN/NCBITaxon:7955 organism context "
                "as selector hints and use record_evidence for one exact supporting quote. Do not "
                "extract chemicals or genes in this step, and do not resolve phenotype ontology IDs "
                "in the extractor."
            ),
            "gene_extractor": (
                "Read the loaded paper and extract exactly one central gene candidate: zebrafish "
                "her1 in the segmentation/small-molecule-screen context. Preserve Danio rerio, "
                "ZFIN, and NCBITaxon:7955 hints and use record_evidence for one exact supporting "
                "quote. Do not extract chemicals or phenotype statements in this step, and do not "
                "resolve gene IDs in the extractor."
            ),
        },
    ),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_since(started_at: float) -> float:
    return round(time.monotonic() - started_at, 3)


def _event_timestamp(event: Mapping[str, Any]) -> datetime | None:
    raw_timestamp = str(event.get("timestamp") or "").strip()
    if not raw_timestamp:
        return None
    if raw_timestamp.endswith("Z"):
        raw_timestamp = f"{raw_timestamp[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_elapsed_seconds(
    start_event: Mapping[str, Any] | None,
    finish_event: Mapping[str, Any] | None,
) -> float | None:
    if start_event is None or finish_event is None:
        return None
    started_at = _event_timestamp(start_event)
    finished_at = _event_timestamp(finish_event)
    if started_at is None or finished_at is None:
        return None
    return round(max(0.0, (finished_at - started_at).total_seconds()), 3)


def _git_metadata() -> dict[str, Any]:
    def _git(args: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        return completed.stdout.strip()

    return {
        "branch": _git(["branch", "--show-current"]),
        "commit": _git(["rev-parse", "HEAD"]),
        "commit_short": _git(["rev-parse", "--short", "HEAD"]),
        "commit_subject": _git(["log", "-1", "--pretty=%s"]),
        "status_short": _git(["status", "--short"]),
    }


def _download_pdf(trial: CorpusTrial, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = download_dir / f"{trial.trial_id}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        return pdf_path
    request = urllib.request.Request(
        trial.pdf_url,
        headers={"User-Agent": "Mozilla/5.0 agr-ai-curation-domain-corpus/1.0"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = response.read()
        content_type = str(response.headers.get("Content-Type", ""))
    if not body.startswith(b"%PDF"):
        raise smoke.SmokeFailure(
            f"Download for {trial.trial_id} did not look like a PDF "
            f"(content_type={content_type!r}, bytes={len(body)})"
        )
    pdf_path.write_bytes(body)
    return pdf_path


def ensure_trial_pdf(trial: CorpusTrial, download_dir: Path) -> Path:
    return _download_pdf(trial, download_dir)


def _agent_step_prompt(trial: CorpusTrial, agent_id: str) -> str:
    if trial.agent_prompts:
        prompt = str(trial.agent_prompts.get(agent_id) or "").strip()
        if prompt:
            return prompt
    return trial.prompt


def build_trial_flow(trial: CorpusTrial) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "id": "task_input_1",
            "type": "task_input",
            "position": {"x": 0, "y": 0},
            "data": {
                "agent_id": "task_input",
                "agent_display_name": "Corpus Trial Instructions",
                "task_instructions": trial.prompt,
                "output_key": "trial_instructions",
                "input_source": "user_query",
            },
        }
    ]
    edges: list[dict[str, str]] = []
    previous_node = "task_input_1"
    for index, agent_id in enumerate(trial.agent_ids, start=1):
        node_id = f"agent_{index}"
        step_prompt = _agent_step_prompt(trial, agent_id)
        nodes.append(
            {
                "id": node_id,
                "type": "agent",
                "position": {"x": 280 * index, "y": 0},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": agent_id.replace("_", " ").title(),
                    "output_key": f"{agent_id}_output",
                    "input_source": "custom",
                    "custom_input": step_prompt,
                    "step_goal": step_prompt,
                },
            }
        )
        edges.append({"id": f"edge_{index}", "source": previous_node, "target": node_id})
        previous_node = node_id
    return {
        "version": "1.0",
        "entry_node_id": "task_input_1",
        "nodes": nodes,
        "edges": edges,
    }


def _selected_trials(names: Iterable[str]) -> list[CorpusTrial]:
    requested = {name.strip() for name in names if name.strip()}
    if not requested:
        return list(TRIALS)
    known = {trial.trial_id for trial in TRIALS} | {trial.domain for trial in TRIALS}
    missing = sorted(requested - known)
    if missing:
        raise smoke.SmokeFailure(f"Unknown trial(s): {', '.join(missing)}")
    return [
        trial
        for trial in TRIALS
        if trial.trial_id in requested or trial.domain in requested
    ]


def _evidence_record_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    record_id = str(record.get("evidence_record_id") or "").strip()
    if record_id:
        return ("id", record_id)
    return (
        "locator",
        str(record.get("entity") or "").strip(),
        str(record.get("chunk_id") or "").strip(),
        str(record.get("verified_quote") or "").strip(),
    )


def _normalized_evidence_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _evidence_record_locator_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("entity") or "").strip().lower(),
        str(record.get("chunk_id") or "").strip(),
        _normalized_evidence_text(record.get("verified_quote")),
    )


def _evidence_record_ids(records: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        record_id
        for record in records
        if (record_id := str(record.get("evidence_record_id") or "").strip())
    }


def _evidence_record_locator_keys(
    records: Iterable[Mapping[str, Any]],
) -> set[tuple[str, str, str]]:
    return {_evidence_record_locator_key(record) for record in records}


def _evidence_record_is_in(
    record: Mapping[str, Any],
    *,
    record_ids: set[str],
    locator_keys: set[tuple[str, str, str]],
) -> bool:
    record_id = str(record.get("evidence_record_id") or "").strip()
    if record_id and record_id in record_ids:
        return True
    return _evidence_record_locator_key(record) in locator_keys


def _evidence_record_matches_attempt(
    record: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> bool:
    record_entity = str(record.get("entity") or "").strip().lower()
    attempt_entity = str(attempt.get("entity") or "").strip().lower()
    if record_entity and attempt_entity and record_entity != attempt_entity:
        return False

    record_chunk_id = str(record.get("chunk_id") or "").strip()
    attempt_chunk_id = str(attempt.get("chunk_id") or "").strip()
    if record_chunk_id and attempt_chunk_id and record_chunk_id != attempt_chunk_id:
        return False

    record_quote = _normalized_evidence_text(record.get("verified_quote"))
    attempt_quote = _normalized_evidence_text(attempt.get("claimed_quote"))
    if not record_quote or not attempt_quote:
        return True
    return record_quote in attempt_quote or attempt_quote in record_quote


def _matching_evidence_record_ids(
    records: Iterable[Mapping[str, Any]],
    attempt: Mapping[str, Any],
) -> list[str]:
    matches: list[str] = []
    attempt_record_id = str(attempt.get("tool_evidence_record_id") or "").strip()
    for record in records:
        record_id = str(record.get("evidence_record_id") or "").strip()
        if attempt_record_id and record_id == attempt_record_id:
            matches.append(record_id)
            continue
        if not _evidence_record_matches_attempt(record, attempt):
            continue
        if record_id:
            matches.append(record_id)
        else:
            matches.append("|".join(_evidence_record_locator_key(record)))
    return matches


def _unique_evidence_records(records: Iterable[Any]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        key = _evidence_record_key(record)
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(record))
    return unique


def _record_evidence_attempts_from_summaries(
    specialist_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for summary in specialist_summaries:
        specialist = summary.get("specialist")
        for call in summary.get("toolCalls") or []:
            if not isinstance(call, Mapping) or call.get("name") != "record_evidence":
                continue
            args = call.get("args") if isinstance(call.get("args"), Mapping) else {}
            output_summary = (
                call.get("outputSummary")
                if isinstance(call.get("outputSummary"), Mapping)
                else {}
            )
            attempts.append(
                {
                    "specialist": specialist,
                    "entity": args.get("entity"),
                    "chunk_id": args.get("chunk_id"),
                    "claimed_quote": args.get("claimed_quote"),
                    "durationMs": call.get("durationMs"),
                    "tool_status": output_summary.get("status"),
                    "tool_message": output_summary.get("message"),
                    "tool_verified_quote": output_summary.get("verified_quote"),
                    "tool_evidence_record_id": output_summary.get("evidence_record_id"),
                    "tool_retry_exhausted": output_summary.get("retry_exhausted"),
                    "tool_terminal": output_summary.get("terminal"),
                    "tool_output_summary": dict(output_summary),
                }
            )
    return attempts


def _entity_counts(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        entity = str(record.get("entity") or "").strip() or "(missing)"
        counts[entity] += 1
    return dict(counts)


def _evidence_usage_audit(
    *,
    specialist_summaries: list[dict[str, Any]],
    evidence_summary_events: list[dict[str, Any]],
    flow_step_evidence_events: list[dict[str, Any]],
) -> dict[str, Any]:
    live_records = _unique_evidence_records(
        record
        for summary in specialist_summaries
        for record in summary.get("liveEvidenceRecords") or []
    )
    retained_records = _unique_evidence_records(
        record
        for summary in specialist_summaries
        for record in summary.get("retainedEvidenceRecords") or []
    )
    evidence_summary_records = _unique_evidence_records(
        record
        for event in evidence_summary_events
        for record in event.get("evidence_records") or []
    )
    flow_step_records = _unique_evidence_records(
        record
        for event in flow_step_evidence_events
        for record in event.get("evidence_records") or []
    )

    retained_ids = _evidence_record_ids(retained_records)
    retained_locator_keys = _evidence_record_locator_keys(retained_records)
    if not retained_ids and not retained_locator_keys:
        retained_ids = _evidence_record_ids(evidence_summary_records)
        retained_locator_keys = _evidence_record_locator_keys(evidence_summary_records)
    if not retained_ids and not retained_locator_keys:
        retained_ids = _evidence_record_ids(flow_step_records)
        retained_locator_keys = _evidence_record_locator_keys(flow_step_records)

    unretained_live_records = [
        record
        for record in live_records
        if not _evidence_record_is_in(
            record,
            record_ids=retained_ids,
            locator_keys=retained_locator_keys,
        )
    ]
    attempts = []
    for attempt in _record_evidence_attempts_from_summaries(specialist_summaries):
        live_matches = _matching_evidence_record_ids(live_records, attempt)
        retained_matches = _matching_evidence_record_ids(retained_records, attempt)
        evidence_summary_matches = _matching_evidence_record_ids(
            evidence_summary_records,
            attempt,
        )
        flow_step_matches = _matching_evidence_record_ids(flow_step_records, attempt)
        attempts.append(
            {
                **attempt,
                "record_evidence_verified": str(
                    attempt.get("tool_status") or ""
                ).strip().lower()
                == "verified",
                "live_verified": bool(live_matches),
                "retained_in_structured_output": bool(retained_matches),
                "present_in_evidence_summary_event": bool(evidence_summary_matches),
                "present_in_flow_step_evidence": bool(flow_step_matches),
                "matched_live_evidence_record_ids": live_matches,
                "matched_retained_evidence_record_ids": retained_matches,
                "matched_evidence_summary_record_ids": evidence_summary_matches,
                "matched_flow_step_evidence_record_ids": flow_step_matches,
            }
        )
    return {
        "record_evidence_attempt_count": len(attempts),
        "record_evidence_attempt_counts_by_entity": _entity_counts(attempts),
        "record_evidence_attempts": attempts,
        "live_verified_evidence_count": len(live_records),
        "live_verified_evidence_counts_by_entity": _entity_counts(live_records),
        "live_verified_evidence_records": live_records,
        "retained_evidence_count": len(retained_records),
        "retained_evidence_counts_by_entity": _entity_counts(retained_records),
        "retained_evidence_records": retained_records,
        "evidence_summary_record_count": len(evidence_summary_records),
        "evidence_summary_record_counts_by_entity": _entity_counts(
            evidence_summary_records
        ),
        "evidence_summary_records": evidence_summary_records,
        "flow_step_evidence_record_count": len(flow_step_records),
        "flow_step_evidence_record_counts_by_entity": _entity_counts(
            flow_step_records
        ),
        "flow_step_evidence_records": flow_step_records,
        "unretained_live_evidence_count": len(unretained_live_records),
        "unretained_live_evidence_counts_by_entity": _entity_counts(
            unretained_live_records
        ),
        "unretained_live_evidence_records": unretained_live_records,
    }


def _event_tool_name(event: dict[str, Any]) -> str:
    return str(((event.get("details") or {}).get("toolName") or "")).strip()


def _summarize_flow_events(flow_result: dict[str, Any]) -> dict[str, Any]:
    events = flow_result.get("events") or []
    event_types = flow_result.get("event_types") or []
    run_finished = flow_result.get("run_finished") or {}
    flow_finished = flow_result.get("flow_finished") or {}
    specialist_summaries = [
        event.get("details") or {}
        for event in events
        if event.get("type") == "SPECIALIST_SUMMARY"
        and isinstance(event.get("details"), dict)
    ]
    flow_step_timings = [
        event.get("details") or {}
        for event in events
        if event.get("type") == "FLOW_STEP_TIMING"
        and isinstance(event.get("details"), dict)
    ]
    flow_validation_group_timings = [
        event.get("details") or {}
        for event in events
        if event.get("type") == "FLOW_VALIDATION_GROUP_TIMING"
        and isinstance(event.get("details"), dict)
    ]
    evidence_summary_events = [
        event
        for event in events
        if event.get("type") == "evidence_summary"
        and isinstance(event, dict)
    ]
    validator_batch_timing_events = [
        event.get("details") or {}
        for event in events
        if (event.get("details") or {}).get("toolName")
        == "dispatch_active_validator_batch"
        and isinstance(event.get("details"), dict)
    ]
    return {
        "event_types": event_types,
        "flow_run_id": flow_result.get("flow_run_id"),
        "total_evidence_records": flow_result.get("total_evidence_records"),
        "run_finished_preview": str(run_finished.get("response") or "")[:4000],
        "run_finished_keys": sorted(run_finished.keys()),
        "flow_finished": flow_finished,
        "flow_step_evidence_events": flow_result.get("flow_step_evidence_events") or [],
        "specialist_summaries": specialist_summaries,
        "flow_step_timings": flow_step_timings,
        "flow_validation_group_timings": flow_validation_group_timings,
        "evidence_summary_events": evidence_summary_events,
        "evidence_usage_audit": _evidence_usage_audit(
            specialist_summaries=specialist_summaries,
            evidence_summary_events=evidence_summary_events,
            flow_step_evidence_events=flow_result.get("flow_step_evidence_events") or [],
        ),
        "validator_batch_timing_events": validator_batch_timing_events,
        "domain_events": [
            event
            for event in events
            if str(event.get("type", "")).startswith("DOMAIN_")
            or "VALIDATOR" in str(event.get("type", ""))
            or str(event.get("type", "")) == "evidence_summary"
            or str(event.get("type", "")) == "FLOW_STEP_TIMING"
            or str(event.get("type", "")) == "FLOW_VALIDATION_GROUP_TIMING"
            or str(event.get("type", "")) == "SPECIALIST_SUMMARY"
            or "validation" in json.dumps(event, sort_keys=True).lower()
            or "lookup_attempt" in json.dumps(event, sort_keys=True).lower()
            or _event_tool_name(event) == "domain_validator_lookup"
            or _event_tool_name(event) == "record_evidence"
            or _event_tool_name(event) == "stage_allele_paper_evidence"
            or _event_tool_name(event) == "finalize_allele_extraction"
            or _event_tool_name(event) == "stage_gene_mention_evidence"
            or _event_tool_name(event) == "finalize_gene_extraction"
            or _event_tool_name(event) == "stage_disease_assertion_evidence"
            or _event_tool_name(event) == "finalize_disease_extraction"
            or _event_tool_name(event) == "stage_chemical_condition_evidence"
            or _event_tool_name(event) == "finalize_chemical_extraction"
            or _event_tool_name(event) == "stage_phenotype_assertion_evidence"
            or _event_tool_name(event) == "finalize_phenotype_extraction"
            or _event_tool_name(event) == "agr_species_context_lookup"
        ],
    }


def _validator_binding_id_from_event(event: dict[str, Any]) -> str:
    details = event.get("details") or {}
    return str(details.get("validatorBindingId") or "").strip()


def _validator_lookup_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup_events: list[dict[str, Any]] = []
    for event in events:
        details = event.get("details") or {}
        tool_name = str(details.get("toolName") or "").strip()
        binding_id = _validator_binding_id_from_event(event)
        if binding_id and tool_name in {"domain_validator_lookup", "agr_curation_query"}:
            lookup_events.append(event)
    return lookup_events


def _validator_request_lifecycle_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if (event.get("details") or {}).get("toolName")
        == "dispatch_active_validator_request"
    ]


def _streamed_validator_lookup_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        event
        for event in _validator_lookup_events(events)
        if (event.get("details") or {}).get("isValidatorInternal") is True
    ]


def _synthetic_validator_lookup_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        event
        for event in _validator_lookup_events(events)
        if (event.get("details") or {}).get("isValidatorInternal") is not True
    ]


def _fallback_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if str(event.get("type") or "").strip() == "SPECIALIST_TEXT_FALLBACK_SUCCESS"
    ]


def _validator_problem_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    problem_events: list[dict[str, Any]] = []
    for event in events:
        event_text = json.dumps(event, sort_keys=True).lower()
        details = event.get("details") or {}
        result_status = str(details.get("validatorResultStatus") or "").strip().lower()
        outcome = str(details.get("outcome") or "").strip().lower()
        if (
            result_status == "error"
            or outcome == "error"
            or "validator_agent_error" in event_text
            or "invalid_schema" in event_text
            or "domain_output_schema_validation_failed" in event_text
            or "builder_finalized_envelope_validation_failed" in event_text
        ):
            problem_events.append(event)
    return problem_events


def _tool_events(
    events: Iterable[dict[str, Any]],
    *,
    tool_name: str,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if (event_type is None or str(event.get("type") or "") == event_type)
        and _event_tool_name(event) == tool_name
    ]


def _builder_observation_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    builder_summaries = [
        event.get("details") or {}
        for event in events
        if event.get("type") == "SPECIALIST_SUMMARY"
        and isinstance(event.get("details"), dict)
        and (event.get("details") or {}).get("builderEnabled") is True
    ]
    zero_validator_events = [
        event
        for event in events
        if (
            (event.get("details") or {}).get("reason") == "no_active_validator_jobs"
            or (event.get("details") or {}).get("validatorDispatchStatus")
            == "no_active_validator_jobs"
        )
    ]
    stage_tool_names = sorted(
        {
            str(details.get("builderStageTool"))
            for details in builder_summaries
            if details.get("builderStageTool")
        }
    )
    finalize_tool_names = sorted(
        {
            str(details.get("builderFinalizeTool"))
            for details in builder_summaries
            if details.get("builderFinalizeTool")
        }
    )
    return {
        "stage_tool_names": stage_tool_names,
        "finalize_tool_names": finalize_tool_names,
        "stage_tool_start_count": sum(
            len(_tool_events(events, tool_name=tool_name, event_type="TOOL_START"))
            for tool_name in stage_tool_names
        ),
        "stage_tool_complete_count": sum(
            len(_tool_events(events, tool_name=tool_name, event_type="TOOL_COMPLETE"))
            for tool_name in stage_tool_names
        ),
        "finalize_tool_start_count": sum(
            len(_tool_events(events, tool_name=tool_name, event_type="TOOL_START"))
            for tool_name in finalize_tool_names
        ),
        "finalize_tool_complete_count": sum(
            len(_tool_events(events, tool_name=tool_name, event_type="TOOL_COMPLETE"))
            for tool_name in finalize_tool_names
        ),
        "builder_summary_count": len(builder_summaries),
        "builder_staged_counts": [
            details.get("builderStagedCount") for details in builder_summaries
        ],
        "builder_finalized_counts": [
            details.get("builderFinalizedCount") for details in builder_summaries
        ],
        "builder_finalization_called": [
            details.get("builderFinalizationCalled") for details in builder_summaries
        ],
        "builder_staged_evidence_ids": [
            details.get("builderStagedEvidenceIds") for details in builder_summaries
        ],
        "builder_finalized_object_counts": [
            details.get("builderFinalizedObjectCount") for details in builder_summaries
        ],
        "builder_validator_target_counts": [
            details.get("builderValidatorTargetCount") for details in builder_summaries
        ],
        "builder_zero_validator_job_statuses": [
            details.get("builderZeroValidatorJobsStatus")
            for details in builder_summaries
        ],
        "zero_validator_job_event_count": len(zero_validator_events),
    }


def _active_validator_dispatch_events(
    events: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start_events: list[dict[str, Any]] = []
    complete_events: list[dict[str, Any]] = []
    for event in events:
        details = event.get("details") or {}
        if details.get("toolName") != "dispatch_active_validator_bindings":
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type == "TOOL_START":
            start_events.append(event)
        elif event_type == "TOOL_COMPLETE":
            complete_events.append(event)
    return start_events, complete_events


def _active_validator_dispatch_duration_seconds(
    events: Iterable[dict[str, Any]],
) -> float | None:
    start_events, complete_events = _active_validator_dispatch_events(events)
    durations = [
        duration
        for start_event, complete_event in zip(start_events, complete_events)
        if (duration := _event_elapsed_seconds(start_event, complete_event)) is not None
    ]
    if not durations:
        return None
    return round(sum(durations), 3)


def _validator_dispatch_completion_details(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    _, complete_events = _active_validator_dispatch_events(events)
    return [
        event.get("details") or {}
        for event in complete_events
        if isinstance(event.get("details"), dict)
    ]


def _first_event_timestamp(events: Iterable[dict[str, Any]]) -> datetime | None:
    timestamps = [
        timestamp
        for event in events
        if (timestamp := _event_timestamp(event)) is not None
    ]
    if not timestamps:
        return None
    return min(timestamps)


def _last_event_timestamp(events: Iterable[dict[str, Any]]) -> datetime | None:
    timestamps = [
        timestamp
        for event in events
        if (timestamp := _event_timestamp(event)) is not None
    ]
    if not timestamps:
        return None
    return max(timestamps)


def _events_started_before_dispatch_complete(
    candidate_events: Iterable[dict[str, Any]],
    all_events: Iterable[dict[str, Any]],
) -> bool | None:
    first_candidate_timestamp = _first_event_timestamp(candidate_events)
    _, dispatch_complete_events = _active_validator_dispatch_events(all_events)
    first_dispatch_complete_timestamp = _first_event_timestamp(dispatch_complete_events)
    if first_candidate_timestamp is None or first_dispatch_complete_timestamp is None:
        return None
    return first_candidate_timestamp < first_dispatch_complete_timestamp


def _validator_lookup_request_keys(events: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for event in events:
        details = event.get("details") or {}
        binding_id = str(details.get("validatorBindingId") or "").strip()
        if not binding_id:
            continue
        request_ids: list[str] = []
        for field_name in (
            "validatorRequestId",
            "validatorRequestIds",
            "validatorLookupRequestIds",
        ):
            value = details.get(field_name)
            if isinstance(value, list):
                request_ids.extend(str(item) for item in value if item is not None)
            elif value is not None:
                request_ids.append(str(value))
        for request_id in request_ids:
            if request_id.strip():
                keys.add((binding_id, request_id.strip()))
    return keys


def _duplicate_synthetic_validator_lookup_event_count(
    events: Iterable[dict[str, Any]],
) -> int:
    event_list = list(events)
    streamed_keys = _validator_lookup_request_keys(
        _streamed_validator_lookup_events(event_list)
    )
    if not streamed_keys:
        return 0
    return sum(
        1
        for event in _synthetic_validator_lookup_events(event_list)
        if _validator_lookup_request_keys([event]) & streamed_keys
    )


def _validator_agent_run_count_from_events(
    events: Iterable[dict[str, Any]],
) -> tuple[int | None, str | None]:
    details = _validator_dispatch_completion_details(events)
    direct_counts = [
        int(value)
        for detail in details
        if (value := detail.get("validatorAgentRunCount")) is not None
    ]
    if direct_counts:
        return sum(direct_counts), "dispatch_completion.validatorAgentRunCount"

    fallback_counts = [
        int(value)
        for detail in details
        if (value := detail.get("validatorResultCount")) is not None
    ]
    if fallback_counts:
        return sum(fallback_counts), "dispatch_completion.validatorResultCount_fallback"
    return None, None


def _batch_validator_run_count_from_events(events: Iterable[dict[str, Any]]) -> int:
    details = _validator_dispatch_completion_details(events)
    direct_counts = [
        int(value)
        for detail in details
        if (value := detail.get("batchValidatorRunCount")) is not None
    ]
    if direct_counts:
        return sum(direct_counts)
    return sum(
        1
        for event in events
        if event.get("type") == "TOOL_COMPLETE"
        and (event.get("details") or {}).get("toolName")
        == "dispatch_active_validator_batch"
    )


def _flow_event_timing_summary(
    *,
    events: list[dict[str, Any]],
    wall_clock_duration_seconds: float,
) -> dict[str, Any]:
    run_started = next((event for event in events if event.get("type") == "RUN_STARTED"), None)
    run_finished = next((event for event in events if event.get("type") == "RUN_FINISHED"), None)
    flow_finished = next((event for event in events if event.get("type") == "FLOW_FINISHED"), None)
    event_duration = (
        _event_elapsed_seconds(run_started, flow_finished)
        or _event_elapsed_seconds(run_started, run_finished)
    )
    specialist_summaries = [
        event.get("details") or {}
        for event in events
        if event.get("type") == "SPECIALIST_SUMMARY"
        and isinstance(event.get("details"), dict)
    ]
    specialist_phase_totals_ms: dict[str, int] = {}
    for summary in specialist_summaries:
        phase_timings = summary.get("phaseTimingsMs")
        if not isinstance(phase_timings, dict):
            continue
        for phase_name, value in phase_timings.items():
            try:
                specialist_phase_totals_ms[str(phase_name)] = (
                    specialist_phase_totals_ms.get(str(phase_name), 0)
                    + int(value or 0)
                )
            except (TypeError, ValueError):
                continue
    flow_step_timings = [
        event.get("details") or {}
        for event in events
        if event.get("type") == "FLOW_STEP_TIMING"
        and isinstance(event.get("details"), dict)
    ]
    flow_validation_group_timings = [
        event.get("details") or {}
        for event in events
        if event.get("type") == "FLOW_VALIDATION_GROUP_TIMING"
        and isinstance(event.get("details"), dict)
    ]
    flow_validation_group_phase_totals_ms: dict[str, int] = {}
    for detail in flow_validation_group_timings:
        phase_timings = detail.get("phaseTimingsMs")
        if not isinstance(phase_timings, dict):
            continue
        for phase_name, value in phase_timings.items():
            try:
                flow_validation_group_phase_totals_ms[str(phase_name)] = (
                    flow_validation_group_phase_totals_ms.get(str(phase_name), 0)
                    + int(value or 0)
                )
            except (TypeError, ValueError):
                continue
    validator_batch_details = [
        event.get("details") or {}
        for event in events
        if (event.get("details") or {}).get("toolName")
        == "dispatch_active_validator_batch"
        and event.get("type") == "TOOL_COMPLETE"
        and isinstance(event.get("details"), dict)
    ]
    return {
        "flow_execution_duration_seconds": (
            event_duration
            if event_duration is not None
            else wall_clock_duration_seconds
        ),
        "flow_execution_duration_source": (
            "sse_event_timestamps" if event_duration is not None else "wall_clock"
        ),
        "flow_execution_wall_clock_seconds": wall_clock_duration_seconds,
        "active_validator_dispatch_duration_seconds": (
            _active_validator_dispatch_duration_seconds(events)
        ),
        "specialist_total_duration_seconds": round(
            sum(int(summary.get("totalDurationMs") or 0) for summary in specialist_summaries)
            / 1000,
            3,
        ),
        "specialist_stream_duration_seconds": round(
            sum(int(summary.get("streamDurationMs") or 0) for summary in specialist_summaries)
            / 1000,
            3,
        ),
        "specialist_phase_totals_seconds": {
            key: round(value / 1000, 3)
            for key, value in sorted(specialist_phase_totals_ms.items())
        },
        "flow_step_timing_seconds": [
            {
                "step": detail.get("step"),
                "tool_name": detail.get("toolName"),
                "total_duration_seconds": round(
                    int(detail.get("totalDurationMs") or 0) / 1000,
                    3,
                ),
                "phase_timings_seconds": {
                    key: round(int(value or 0) / 1000, 3)
                    for key, value in (
                        (detail.get("phaseTimingsMs") or {}).items()
                        if isinstance(detail.get("phaseTimingsMs"), dict)
                        else []
                    )
                },
            }
            for detail in flow_step_timings
        ],
        "flow_validation_group_timing_seconds": [
            {
                "status": detail.get("status"),
                "total_duration_seconds": round(
                    int(detail.get("totalDurationMs") or 0) / 1000,
                    3,
                ),
                "phase_timings_seconds": {
                    key: round(int(value or 0) / 1000, 3)
                    for key, value in (
                        (detail.get("phaseTimingsMs") or {}).items()
                        if isinstance(detail.get("phaseTimingsMs"), dict)
                        else []
                    )
                },
                "group_count": detail.get("groupCount"),
                "executable_group_count": detail.get("executableGroupCount"),
                "materialization_input_count": detail.get("materializationInputCount"),
                "selector_finding_count": detail.get("selectorFindingCount"),
                "appended_finding_count": detail.get("appendedFindingCount"),
            }
            for detail in flow_validation_group_timings
        ],
        "flow_validation_group_phase_totals_seconds": {
            key: round(value / 1000, 3)
            for key, value in sorted(flow_validation_group_phase_totals_ms.items())
        },
        "validator_batch_timing_seconds": [
            {
                "validator_binding_id": detail.get("validatorBindingId"),
                "request_count": detail.get("validatorBatchRequestCount"),
                "duration_seconds": detail.get("validatorBatchDurationSeconds"),
                "runner_duration_seconds": detail.get(
                    "validatorBatchRunnerDurationSeconds"
                ),
                "output_validation_duration_seconds": detail.get(
                    "validatorBatchOutputValidationDurationSeconds"
                ),
            }
            for detail in validator_batch_details
        ],
    }


def _validator_observation_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    lookup_events = _validator_lookup_events(events)
    request_events = _validator_request_lifecycle_events(events)
    request_start_events = [
        event for event in request_events if event.get("type") == "TOOL_START"
    ]
    request_complete_events = [
        event for event in request_events if event.get("type") == "TOOL_COMPLETE"
    ]
    streamed_lookup_events = _streamed_validator_lookup_events(events)
    synthetic_lookup_events = _synthetic_validator_lookup_events(events)
    problem_events = _validator_problem_events(events)
    fallback_events = _fallback_events(events)
    observed_counts = Counter(
        binding_id
        for event in lookup_events
        if (binding_id := _validator_binding_id_from_event(event))
    )
    validator_agent_run_count, count_source = _validator_agent_run_count_from_events(events)
    return {
        "validator_event_count": sum(
            1
            for event in events
            if "validator" in json.dumps(event, sort_keys=True).lower()
        ),
        "validator_lookup_event_count": len(lookup_events),
        "streamed_validator_lookup_event_count": len(streamed_lookup_events),
        "synthetic_validator_lookup_event_count": len(synthetic_lookup_events),
        "duplicate_synthetic_validator_lookup_event_count": (
            _duplicate_synthetic_validator_lookup_event_count(events)
        ),
        "validator_request_event_count": len(request_events),
        "validator_request_start_event_count": len(request_start_events),
        "validator_request_complete_event_count": len(request_complete_events),
        "validator_request_rows_before_dispatch_complete": (
            _events_started_before_dispatch_complete(request_events, events)
        ),
        "streamed_validator_lookup_rows_before_dispatch_complete": (
            _events_started_before_dispatch_complete(streamed_lookup_events, events)
        ),
        "batch_validator_run_count": _batch_validator_run_count_from_events(events),
        "validator_problem_event_count": len(problem_events),
        "specialist_text_fallback_event_count": len(fallback_events),
        "observed_validator_lookup_counts": dict(sorted(observed_counts.items())),
        "validator_agent_run_count": validator_agent_run_count,
        "validator_agent_run_count_source": count_source,
        "validator_dispatch_completion_details": _validator_dispatch_completion_details(events),
        "builder_observations": _builder_observation_summary(events),
        "validator_first_request_event_timestamp": (
            _first_event_timestamp(request_events).isoformat()
            if _first_event_timestamp(request_events)
            else None
        ),
        "validator_last_request_event_timestamp": (
            _last_event_timestamp(request_events).isoformat()
            if _last_event_timestamp(request_events)
            else None
        ),
    }


def validate_tightened_trial_gate(
    *,
    trial: CorpusTrial,
    flow_result: dict[str, Any],
    checks: list[dict[str, Any]],
    allow_specialist_text_fallback: bool,
) -> dict[str, Any]:
    events = flow_result.get("events") or []
    lookup_events = _validator_lookup_events(events)
    request_events = _validator_request_lifecycle_events(events)
    fallback_events = _fallback_events(events)
    problem_events = _validator_problem_events(events)
    validator_agent_run_count, _count_source = _validator_agent_run_count_from_events(events)
    batch_validator_run_count = _batch_validator_run_count_from_events(events)
    observed_counts = Counter(
        binding_id
        for event in lookup_events
        if (binding_id := _validator_binding_id_from_event(event))
    )
    expected_bindings = tuple(trial.expected_validator_bindings)
    minimum_count = (
        trial.minimum_expected_validator_bindings
        if trial.minimum_expected_validator_bindings is not None
        else len(expected_bindings)
    )
    observed_expected_bindings = [
        binding_id for binding_id in expected_bindings if observed_counts.get(binding_id, 0) > 0
    ]
    missing_expected_bindings = [
        binding_id for binding_id in expected_bindings if observed_counts.get(binding_id, 0) <= 0
    ]
    enough_expected_bindings = len(observed_expected_bindings) >= minimum_count
    singleton_validator_run_count = max(
        0,
        int(validator_agent_run_count or 0) - batch_validator_run_count,
    )
    request_lifecycle_ok = (
        singleton_validator_run_count == 0
        or len(request_events) >= singleton_validator_run_count * 2
    )
    fallback_ok = allow_specialist_text_fallback or not fallback_events
    no_problem_events = not problem_events
    builder_observations = _builder_observation_summary(events)
    builder_ok = True
    if trial.domain in BUILDER_REQUIRED_DOMAINS:
        finalized_object_counts = [
            int(value)
            for value in builder_observations["builder_finalized_object_counts"]
            if isinstance(value, int)
        ]
        validator_target_counts = [
            int(value)
            for value in builder_observations["builder_validator_target_counts"]
            if isinstance(value, int)
        ]
        zero_statuses = [
            str(value)
            for value in builder_observations["builder_zero_validator_job_statuses"]
            if value is not None
        ]
        builder_ok = (
            builder_observations["stage_tool_complete_count"] >= 1
            and builder_observations["finalize_tool_complete_count"] == 1
            and builder_observations["builder_summary_count"] >= 1
            and any(builder_observations["builder_finalization_called"])
            and max(finalized_object_counts or [0]) > 0
            and max(validator_target_counts or [0]) > 0
            and "no_active_validator_jobs" not in zero_statuses
            and builder_observations["zero_validator_job_event_count"] == 0
        )
    ok = (
        enough_expected_bindings
        and request_lifecycle_ok
        and fallback_ok
        and no_problem_events
        and builder_ok
    )
    payload = {
        "expected_validator_bindings": expected_bindings,
        "minimum_expected_validator_bindings": minimum_count,
        "observed_validator_lookup_counts": dict(sorted(observed_counts.items())),
        "observed_expected_validator_bindings": observed_expected_bindings,
        "missing_expected_validator_bindings": missing_expected_bindings,
        "validator_request_event_count": len(request_events),
        "validator_agent_run_count": validator_agent_run_count,
        "batch_validator_run_count": batch_validator_run_count,
        "singleton_validator_run_count": singleton_validator_run_count,
        "request_lifecycle_ok": request_lifecycle_ok,
        "specialist_text_fallback_event_count": len(fallback_events),
        "validator_problem_event_count": len(problem_events),
        "allow_specialist_text_fallback": allow_specialist_text_fallback,
        "builder_observations": builder_observations,
    }
    checks.append(
        {
            "step": f"{trial.trial_id}_tightened_validator_audit_gate",
            "ok": ok,
            "payload": payload,
        }
    )
    if not ok:
        reasons: list[str] = []
        if not enough_expected_bindings:
            reasons.append(
                "missing validator audit events for "
                f"{missing_expected_bindings}; observed {dict(sorted(observed_counts.items()))}"
            )
        if not request_lifecycle_ok:
            reasons.append(
                "missing validator request lifecycle events: "
                f"request_events={len(request_events)}, "
                f"singleton_validator_run_count={singleton_validator_run_count}"
            )
        if not fallback_ok:
            reasons.append(
                f"specialist text fallback events present: {len(fallback_events)}"
            )
        if not no_problem_events:
            reasons.append(
                f"validator error/invalid-schema events present: {len(problem_events)}"
            )
        if not builder_ok:
            reasons.append(
                f"{trial.domain} builder gate failed: "
                + json.dumps(builder_observations, sort_keys=True)
            )
        raise smoke.SmokeFailure(
            f"Tightened corpus gate failed for {trial.trial_id}: " + "; ".join(reasons)
        )
    return payload


def execute_flow_permissive(
    *,
    base_url: str,
    headers: dict[str, str],
    flow_id: str,
    document_id: str,
    user_query: str,
    flow_timeout_seconds: float,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    started_at = time.monotonic()
    response = smoke.http_request(
        "POST",
        f"{base_url}/api/chat/execute-flow",
        headers=headers,
        json_body={
            "flow_id": flow_id,
            "session_id": f"corpus-flow-{uuid4().hex[:8]}",
            "document_id": document_id,
            "user_query": user_query,
        },
        timeout=flow_timeout_seconds,
    )
    wall_clock_duration_seconds = _duration_since(started_at)
    smoke.require(
        response.status_code == 200,
        f"Unexpected execute-flow response: {response.status_code} {response.text}",
    )
    events = smoke.parse_sse_events(response.text)
    smoke.require(events, "Flow execution returned no SSE events")
    event_types = [str(event.get("type", "")) for event in events]
    error_events = smoke.collect_error_events(events)

    flow_finished = next((event for event in events if event.get("type") == "FLOW_FINISHED"), {})
    run_started = next((event for event in events if event.get("type") == "RUN_STARTED"), {})
    run_finished = next((event for event in events if event.get("type") == "RUN_FINISHED"), {})
    terminal_status = str(flow_finished.get("status") or "").strip().lower()
    flow_run_id = str(flow_finished.get("flow_run_id") or "").strip()
    total_evidence_records = int(flow_finished.get("total_evidence_records") or 0)
    flow_step_evidence_events = [
        event for event in events if event.get("type") == "FLOW_STEP_EVIDENCE"
    ]
    summary = {
        "events": events,
        "event_types": event_types,
        "run_started": run_started,
        "run_finished": run_finished,
        "flow_finished": flow_finished,
        "flow_run_id": flow_run_id,
        "total_evidence_records": total_evidence_records,
        "flow_step_evidence_events": flow_step_evidence_events,
        "timing": _flow_event_timing_summary(
            events=events,
            wall_clock_duration_seconds=wall_clock_duration_seconds,
        ),
        "validator_observations": _validator_observation_summary(events),
        "error_events": error_events,
    }
    checks.append(
        {
            "step": "execute_flow_permissive",
            "ok": not error_events
            and terminal_status == "completed"
            and total_evidence_records > 0,
            "status_code": response.status_code,
            "payload": {
                "event_types": event_types,
                "run_started": run_started,
                "run_finished": run_finished,
                "flow_finished": flow_finished,
                "error_events": error_events,
                "flow_step_evidence_events": flow_step_evidence_events,
                "zero_evidence_warning": total_evidence_records == 0,
                "timing": summary["timing"],
                "validator_observations": summary["validator_observations"],
            },
        }
    )
    if error_events:
        raise FlowExecutionFailure(
            f"Flow execution emitted error events: {error_events}",
            summary=summary,
        )
    if terminal_status != "completed":
        raise FlowExecutionFailure(
            f"Flow did not complete successfully: {flow_finished}",
            summary=summary,
        )
    return summary


def _annotate_note_with_flow_result(
    note: dict[str, Any],
    flow_result: dict[str, Any],
) -> None:
    flow_timing = flow_result.get("timing") or {}
    validator_observations = flow_result.get("validator_observations") or {}
    note["flow_execution_duration_seconds"] = flow_timing.get(
        "flow_execution_duration_seconds"
    )
    note["flow_execution_duration_source"] = flow_timing.get(
        "flow_execution_duration_source"
    )
    note["flow_execution_wall_clock_seconds"] = flow_timing.get(
        "flow_execution_wall_clock_seconds"
    )
    note["active_validator_dispatch_duration_seconds"] = flow_timing.get(
        "active_validator_dispatch_duration_seconds"
    )
    for key in (
        "specialist_total_duration_seconds",
        "specialist_stream_duration_seconds",
        "specialist_phase_totals_seconds",
        "flow_step_timing_seconds",
        "flow_validation_group_timing_seconds",
        "flow_validation_group_phase_totals_seconds",
        "validator_batch_timing_seconds",
    ):
        if key in flow_timing:
            note[key] = flow_timing[key]
    for key in (
        "validator_event_count",
        "validator_lookup_event_count",
        "streamed_validator_lookup_event_count",
        "synthetic_validator_lookup_event_count",
        "duplicate_synthetic_validator_lookup_event_count",
        "validator_request_event_count",
        "validator_request_start_event_count",
        "validator_request_complete_event_count",
        "validator_request_rows_before_dispatch_complete",
        "streamed_validator_lookup_rows_before_dispatch_complete",
        "batch_validator_run_count",
        "validator_problem_event_count",
        "specialist_text_fallback_event_count",
        "observed_validator_lookup_counts",
        "validator_agent_run_count",
        "validator_agent_run_count_source",
        "validator_dispatch_completion_details",
        "builder_observations",
        "validator_first_request_event_timestamp",
        "validator_last_request_event_timestamp",
    ):
        if key in validator_observations:
            note[key] = validator_observations[key]
    note["flow_summary"] = _summarize_flow_events(flow_result)


def run_trial(
    *,
    trial: CorpusTrial,
    base_url: str,
    headers: dict[str, str],
    download_dir: Path,
    output_dir: Path,
    checks: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    trial_started_at = time.monotonic()
    pdf_path = ensure_trial_pdf(trial, download_dir)
    upload_pdf_path = download_dir / f"{trial.trial_id}-{uuid4().hex[:8]}.pdf"
    if not upload_pdf_path.exists():
        upload_pdf_path.write_bytes(pdf_path.read_bytes())
    note: dict[str, Any] = {
        "trial": asdict(trial),
        "started_at": _now_iso(),
        "pdf_path": str(pdf_path),
        "upload_pdf_path": str(upload_pdf_path),
        "pdf_size_bytes": pdf_path.stat().st_size,
        "document_id": None,
        "flow_id": None,
        "status": "started",
        "checks": [],
        "duration_seconds": None,
        "upload_duration_seconds": None,
        "processing_duration_seconds": None,
        "flow_execution_duration_seconds": None,
        "flow_execution_duration_source": None,
        "flow_execution_wall_clock_seconds": None,
        "active_validator_dispatch_duration_seconds": None,
        "validator_event_count": 0,
        "validator_lookup_event_count": 0,
        "streamed_validator_lookup_event_count": 0,
        "synthetic_validator_lookup_event_count": 0,
        "duplicate_synthetic_validator_lookup_event_count": 0,
        "validator_request_event_count": 0,
        "validator_request_start_event_count": 0,
        "validator_request_complete_event_count": 0,
        "validator_request_rows_before_dispatch_complete": None,
        "streamed_validator_lookup_rows_before_dispatch_complete": None,
        "batch_validator_run_count": 0,
        "validator_problem_event_count": 0,
        "specialist_text_fallback_event_count": 0,
        "observed_validator_lookup_counts": {},
        "validator_agent_run_count": None,
        "validator_agent_run_count_source": None,
        "builder_observations": {},
    }
    trial_checks: list[dict[str, Any]] = note["checks"]
    document_id: str | None = None
    flow_id: str | None = None
    try:
        if args.delete_existing_sample_documents:
            smoke.delete_matching_documents(
                base_url=base_url,
                filename=upload_pdf_path.name,
                headers=headers,
                checks=trial_checks,
                step_prefix=f"{trial.trial_id}_delete_existing",
            )
        upload_started_at = time.monotonic()
        document_id, created = smoke.upload_pdf(
            base_url=base_url,
            sample_pdf=upload_pdf_path,
            headers=headers,
            checks=trial_checks,
            can_reuse_duplicate=args.allow_duplicate_reuse,
            step_name=f"{trial.trial_id}_upload",
        )
        note["upload_duration_seconds"] = _duration_since(upload_started_at)
        note["document_id"] = document_id
        note["created_document"] = created
        processing_started_at = time.monotonic()
        smoke.wait_for_processing_complete(
            base_url=base_url,
            document_id=document_id,
            headers=headers,
            processing_timeout_seconds=args.processing_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            checks=trial_checks,
            step_name=f"{trial.trial_id}_processing_complete",
        )
        note["processing_duration_seconds"] = _duration_since(processing_started_at)
        smoke.fetch_chunks(
            base_url=base_url,
            document_id=document_id,
            headers=headers,
            checks=trial_checks,
            step_name=f"{trial.trial_id}_chunks",
        )
        flow_id = smoke.create_flow(
            base_url=base_url,
            headers=headers,
            name=f"Corpus {trial.trial_id} {uuid4().hex[:6]}",
            description=f"Real-PDF corpus trial for {trial.domain}",
            flow_definition=build_trial_flow(trial),
            checks=trial_checks,
            step_name=f"{trial.trial_id}_flow_create",
        )
        note["flow_id"] = flow_id
        try:
            flow_result = execute_flow_permissive(
                base_url=base_url,
                headers=headers,
                flow_id=flow_id,
                document_id=document_id,
                user_query=trial.prompt,
                flow_timeout_seconds=args.flow_timeout_seconds,
                checks=trial_checks,
            )
        except FlowExecutionFailure as exc:
            _annotate_note_with_flow_result(note, exc.summary)
            raise

        _annotate_note_with_flow_result(note, flow_result)
        zero_evidence_records = int(flow_result.get("total_evidence_records") or 0) == 0
        if zero_evidence_records:
            note.setdefault("warnings", []).append("flow_completed_with_zero_persisted_evidence_records")
        if flow_result.get("flow_run_id"):
            try:
                note["flow_evidence_export"] = smoke.export_flow_evidence_json(
                    base_url=base_url,
                    headers=headers,
                    flow_run_id=str(flow_result["flow_run_id"]),
                    checks=trial_checks,
                )
            except Exception as exc:
                note.setdefault("warnings", []).append(f"flow_evidence_export_failed: {exc}")
        try:
            note["tightened_validator_audit_gate"] = validate_tightened_trial_gate(
                trial=trial,
                flow_result=flow_result,
                checks=trial_checks,
                allow_specialist_text_fallback=args.allow_specialist_text_fallback,
            )
        except smoke.SmokeFailure:
            gate_step = f"{trial.trial_id}_tightened_validator_audit_gate"
            gate_check = next(
                (
                    check
                    for check in reversed(trial_checks)
                    if check.get("step") == gate_step
                ),
                None,
            )
            if gate_check is not None:
                note["tightened_validator_audit_gate"] = gate_check.get("payload")
            raise
        if zero_evidence_records:
            raise smoke.SmokeFailure(
                "Flow completed with zero persisted evidence records"
            )
        note["status"] = "pass"
    except Exception as exc:
        note["status"] = "fail"
        note["error"] = str(exc)
    finally:
        if args.cleanup_documents and document_id:
            try:
                smoke.http_request(
                    "DELETE",
                    f"{base_url}/weaviate/documents/{urllib.parse.quote(document_id)}",
                    headers=headers,
                    timeout=30.0,
                )
            except Exception as exc:
                note["cleanup_error"] = str(exc)
        note["finished_at"] = _now_iso()
        note["duration_seconds"] = _duration_since(trial_started_at)
        output_dir.mkdir(parents=True, exist_ok=True)
        note_path = output_dir / f"{trial.trial_id}.json"
        note_path.write_text(json.dumps(note, indent=2, sort_keys=True), encoding="utf-8")
        note["note_path"] = str(note_path)
        checks.extend(trial_checks)
    return note


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--download-dir", default=str(DEFAULT_DOWNLOAD_DIR))
    parser.add_argument("--trial", action="append", default=[], help="Trial id or domain to run; may repeat")
    parser.add_argument("--wake-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--processing-timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--flow-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=3.0)
    parser.add_argument("--allow-dev-mode-fallback", action="store_true")
    parser.add_argument("--allow-duplicate-reuse", action="store_true")
    parser.add_argument("--delete-existing-sample-documents", action="store_true")
    parser.add_argument("--cleanup-documents", action="store_true")
    parser.add_argument(
        "--allow-specialist-text-fallback",
        action="store_true",
        help=(
            "Debug-only relaxation: record but do not fail SPECIALIST_TEXT_FALLBACK_SUCCESS "
            "events. Validator lookup audit events are still required."
        ),
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    base_url = args.base_url.rstrip("/")
    env_file = Path(args.env_file).expanduser()
    api_key = smoke.resolve_api_key(args.api_key, env_file)
    smoke.verify_api_key_mode(api_key, allow_dev_mode_fallback=args.allow_dev_mode_fallback)
    headers = smoke.build_headers(api_key)
    output_dir = Path(args.output_dir)
    download_dir = Path(args.download_dir)
    checks: list[dict[str, Any]] = []
    selected = _selected_trials(args.trial)

    payload: dict[str, Any] = {
        "timestamp_utc": _now_iso(),
        "base_url": base_url,
        "output_dir": str(output_dir),
        "repo": _git_metadata(),
        "trial_ids": [trial.trial_id for trial in selected],
        "checks": checks,
        "results": [],
    }

    smoke.print_step("Checking backend health")
    health_response = smoke.http_request("GET", f"{base_url}/health", headers=headers, timeout=20.0)
    smoke.require(
        health_response.status_code == 200 and isinstance(health_response.json_body, dict),
        f"Unexpected /health response: {health_response.status_code} {health_response.text}",
    )
    payload["health"] = health_response.json_body
    if api_key:
        smoke.check_current_user(
            base_url=base_url,
            headers=headers,
            checks=checks,
            expected_auth_sub=None,
            expected_email=None,
        )
    else:
        user_response = smoke.http_request(
            "GET",
            f"{base_url}/api/users/me",
            headers=headers,
            timeout=20.0,
        )
        smoke.require(
            user_response.status_code == 200 and isinstance(user_response.json_body, dict),
            f"Unexpected current-user response: {user_response.status_code} {user_response.text}",
        )
        checks.append(
            {
                "step": "current_user_dev_mode",
                "ok": True,
                "status_code": user_response.status_code,
                "payload": user_response.json_body,
            }
        )
    smoke.check_llm_provider_health(base_url=base_url, headers=headers, checks=checks)
    smoke.ensure_worker_ready(
        base_url=base_url,
        headers=headers,
        wake_timeout_seconds=args.wake_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        checks=checks,
    )

    for trial in selected:
        smoke.print_step(f"Running corpus trial {trial.trial_id}")
        payload["results"].append(
            run_trial(
                trial=trial,
                base_url=base_url,
                headers=headers,
                download_dir=download_dir,
                output_dir=output_dir,
                checks=checks,
                args=args,
            )
        )

    payload["overall_status"] = (
        "pass" if all(result.get("status") == "pass" for result in payload["results"]) else "fail"
    )
    payload["trial_timing_summary"] = [
        {
            "trial_id": result.get("trial", {}).get("trial_id"),
            "status": result.get("status"),
            "duration_seconds": result.get("duration_seconds"),
            "upload_duration_seconds": result.get("upload_duration_seconds"),
            "processing_duration_seconds": result.get("processing_duration_seconds"),
            "flow_execution_duration_seconds": result.get("flow_execution_duration_seconds"),
            "active_validator_dispatch_duration_seconds": result.get(
                "active_validator_dispatch_duration_seconds"
            ),
            "validator_agent_run_count": result.get("validator_agent_run_count"),
            "validator_request_event_count": result.get("validator_request_event_count"),
            "validator_request_rows_before_dispatch_complete": result.get(
                "validator_request_rows_before_dispatch_complete"
            ),
            "validator_lookup_event_count": result.get("validator_lookup_event_count"),
            "streamed_validator_lookup_event_count": result.get(
                "streamed_validator_lookup_event_count"
            ),
            "synthetic_validator_lookup_event_count": result.get(
                "synthetic_validator_lookup_event_count"
            ),
            "duplicate_synthetic_validator_lookup_event_count": result.get(
                "duplicate_synthetic_validator_lookup_event_count"
            ),
            "streamed_validator_lookup_rows_before_dispatch_complete": result.get(
                "streamed_validator_lookup_rows_before_dispatch_complete"
            ),
            "batch_validator_run_count": result.get("batch_validator_run_count"),
            "validator_problem_event_count": result.get("validator_problem_event_count"),
            "specialist_text_fallback_event_count": result.get(
                "specialist_text_fallback_event_count"
            ),
            "observed_validator_lookup_counts": result.get(
                "observed_validator_lookup_counts"
            ),
        }
        for result in payload["results"]
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    smoke.print_step(f"Corpus summary: {summary_path}")
    return 0 if payload["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
