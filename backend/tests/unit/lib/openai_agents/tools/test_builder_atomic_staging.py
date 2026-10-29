"""Real staging tools preserve distinct records when SDK workers run in parallel."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from agr_ai_curation_alliance.tools import gene_builder_tools
from src.lib.openai_agents import extraction_builder_workspace as builder


def test_parallel_gene_stage_tools_keep_all_records(monkeypatch):
    workspace = builder.ExtractionBuilderWorkspace(run_id="parallel-gene-staging")
    monkeypatch.setattr(gene_builder_tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(gene_builder_tools, "write_extraction_trace_event", lambda **event: event)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: event)
    start = Barrier(8, timeout=10)

    def stage(index):
        start.wait()
        return gene_builder_tools._stage_gene_mention_evidence_impl(
            pending_ref_id=f"gene-{index}", mention=f"mention-{index}",
            evidence_record_ids=[f"evidence-{index}"],
            identity_resolution_notes=["Separate source mention"],
            confidence="high", rationale="Retain this distinct source mention",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(stage, range(8)))

    assert all(result.status == "ok" for result in results)
    assert len(workspace.candidates) == 8
    assert len({result.data["candidate_id"] for result in results}) == 8
    for index, result in enumerate(results):
        candidate = workspace.get_candidate(result.data["candidate_id"])
        assert candidate.candidate_id.startswith("gene-candidate-")
        assert candidate.pending_ref_ids == [f"gene-{index}"]
        assert candidate.staged_fields["mention"] == f"mention-{index}"
        assert candidate.evidence_record_ids == [f"evidence-{index}"]
