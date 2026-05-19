#!/usr/bin/env python3
"""Run real-PDF domain-envelope corpus trials against a live backend."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import dev_release_smoke as smoke


DEFAULT_BASE_URL = "http://192.168.86.44:8900"
DEFAULT_OUTPUT_DIR = Path("docs/design/pdf-corpus-trials")
DEFAULT_DOWNLOAD_DIR = Path("/tmp/agr_domain_envelope_pdf_corpus")


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
            "Run a compact cross-domain pass over this zebrafish segmentation paper. Extract at "
            "most one chemical condition, one phenotype statement, and one central gene if the "
            "paper supports it. Each extractor must use record_evidence for one exact supporting "
            "quote and must leave identity/ontology resolution to validators."
        ),
    ),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        nodes.append(
            {
                "id": node_id,
                "type": "agent",
                "position": {"x": 280 * index, "y": 0},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": agent_id.replace("_", " ").title(),
                    "output_key": f"{agent_id}_output",
                    "input_source": "previous_output",
                    "step_goal": trial.prompt,
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


def _summarize_flow_events(flow_result: dict[str, Any]) -> dict[str, Any]:
    events = flow_result.get("events") or []
    event_types = flow_result.get("event_types") or []
    run_finished = flow_result.get("run_finished") or {}
    flow_finished = flow_result.get("flow_finished") or {}
    return {
        "event_types": event_types,
        "flow_run_id": flow_result.get("flow_run_id"),
        "total_evidence_records": flow_result.get("total_evidence_records"),
        "run_finished_preview": str(run_finished.get("response") or "")[:4000],
        "run_finished_keys": sorted(run_finished.keys()),
        "flow_finished": flow_finished,
        "flow_step_evidence_events": flow_result.get("flow_step_evidence_events") or [],
        "domain_events": [
            event
            for event in events
            if str(event.get("type", "")).startswith("DOMAIN_")
            or "VALIDATOR" in str(event.get("type", ""))
            or "validation" in json.dumps(event, sort_keys=True).lower()
            or "lookup_attempt" in json.dumps(event, sort_keys=True).lower()
            or str(((event.get("details") or {}).get("toolName") or "")) == "record_evidence"
            or str(((event.get("details") or {}).get("toolName") or "")) == "agr_species_context_lookup"
        ],
    }


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
    smoke.require(
        response.status_code == 200,
        f"Unexpected execute-flow response: {response.status_code} {response.text}",
    )
    events = smoke.parse_sse_events(response.text)
    smoke.require(events, "Flow execution returned no SSE events")
    event_types = [str(event.get("type", "")) for event in events]
    error_events = smoke.collect_error_events(events)
    smoke.require(not error_events, f"Flow execution emitted error events: {error_events}")

    flow_finished = next((event for event in events if event.get("type") == "FLOW_FINISHED"), {})
    run_started = next((event for event in events if event.get("type") == "RUN_STARTED"), {})
    run_finished = next((event for event in events if event.get("type") == "RUN_FINISHED"), {})
    terminal_status = str(flow_finished.get("status") or "").strip().lower()
    smoke.require(terminal_status == "completed", f"Flow did not complete successfully: {flow_finished}")
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
    }
    checks.append(
        {
            "step": "execute_flow_permissive",
            "ok": total_evidence_records > 0,
            "status_code": response.status_code,
            "payload": {
                "event_types": event_types,
                "run_started": run_started,
                "run_finished": run_finished,
                "flow_finished": flow_finished,
                "flow_step_evidence_events": flow_step_evidence_events,
                "zero_evidence_warning": total_evidence_records == 0,
            },
        }
    )
    return summary


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
        document_id, created = smoke.upload_pdf(
            base_url=base_url,
            sample_pdf=upload_pdf_path,
            headers=headers,
            checks=trial_checks,
            can_reuse_duplicate=args.allow_duplicate_reuse,
            step_name=f"{trial.trial_id}_upload",
        )
        note["document_id"] = document_id
        note["created_document"] = created
        smoke.wait_for_processing_complete(
            base_url=base_url,
            document_id=document_id,
            headers=headers,
            processing_timeout_seconds=args.processing_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            checks=trial_checks,
            step_name=f"{trial.trial_id}_processing_complete",
        )
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
        flow_result = execute_flow_permissive(
            base_url=base_url,
            headers=headers,
            flow_id=flow_id,
            document_id=document_id,
            user_query=trial.prompt,
            flow_timeout_seconds=args.flow_timeout_seconds,
            checks=trial_checks,
        )
        note["flow_summary"] = _summarize_flow_events(flow_result)
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
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    smoke.print_step(f"Corpus summary: {summary_path}")
    return 0 if payload["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
