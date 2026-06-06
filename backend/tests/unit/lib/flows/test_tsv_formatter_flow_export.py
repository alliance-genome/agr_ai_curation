import importlib
import json

import pytest


def _executor_module():
    return importlib.import_module("src.lib.flows.executor")


def _completed_artifact_step():
    executor = _executor_module()
    payload = {
        "domain_pack_id": "gene",
        "envelope_id": "env-gene-1",
        "objects": [
            {"object_type": "Gene", "symbol": "TP53"},
            {"object_type": "Gene", "symbol": "BRCA1"},
        ],
    }
    return {
        "step": 1,
        "agent_id": "gene",
        "agent_name": "Gene Specialist",
        "output_preview": "Saved gene candidates for TP53 and BRCA1.",
        "candidate": executor.ExtractionEnvelopeCandidate(
            agent_key="gene",
            payload_json=payload,
            candidate_count=2,
            adapter_key="gene",
            conversation_summary="Extracted two gene candidates.",
        ),
    }


def test_build_flow_artifact_tsv_rows_uses_completed_structured_artifacts():
    executor = _executor_module()

    rows = executor._build_flow_artifact_tsv_rows(
        [
            {"step": 0, "agent_id": "chat_output", "output": "unstructured"},
            _completed_artifact_step(),
        ]
    )

    assert rows == [
        {
            "step": "1",
            "agent_id": "gene",
            "agent_name": "Gene Specialist",
            "adapter_key": "gene",
            "domain_pack_id": "gene",
            "envelope_id": "env-gene-1",
            "object_count": "2",
            "candidate_count": "2",
            "artifact_preview": "Saved gene candidates for TP53 and BRCA1.",
        }
    ]


@pytest.mark.asyncio
async def test_tsv_formatter_flow_output_saves_artifacts_without_model_round_trip(
    monkeypatch,
):
    executor = _executor_module()
    save_calls = []

    async def _fake_save_tsv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "filename": filename,
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-artifact-tsv",
            "filename": "flow_artifacts.tsv",
            "format": "tsv",
            "size_bytes": 1234,
            "hash_sha256": "hash",
            "mime_type": "text/tab-separated-values",
            "download_url": "/api/files/file-artifact-tsv/download",
            "created_at": "2026-04-26T00:00:00Z",
            "trace_id": "trace-1",
            "session_id": "session-1",
            "curator_id": "curator@example.org",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_tsv_impl",
        _fake_save_tsv_impl,
    )

    result_text = await executor._try_save_tsv_formatter_flow_output(
        agent_id="tsv_formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="ALL-303 TSV Regression",
    )

    result = json.loads(result_text or "{}")

    assert len(save_calls) == 1
    assert save_calls[0]["data"] == executor._build_flow_artifact_tsv_rows(
        [_completed_artifact_step()]
    )
    assert save_calls[0]["columns"] == executor._FLOW_ARTIFACT_TSV_COLUMNS
    assert save_calls[0]["filename"] == "ALL-303_TSV_Regression_tsv_export"
    assert result["format"] == "tsv"
    assert result["download_url"].endswith("/download")


@pytest.mark.asyncio
async def test_tsv_formatter_flow_output_skips_when_no_artifacts(monkeypatch):
    executor = _executor_module()

    async def _unexpected_save_tsv_impl(*args, **kwargs):
        raise AssertionError("TSV output should not be saved without artifacts")

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_tsv_impl",
        _unexpected_save_tsv_impl,
    )

    result = await executor._try_save_tsv_formatter_flow_output(
        agent_id="tsv_formatter",
        completed_steps=[{"step": 1, "output": "plain text"}],
        flow_name="No Artifacts",
    )

    assert result is None
