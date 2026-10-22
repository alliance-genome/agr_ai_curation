"""Measured export stages and durable diagnostic receipts (ALL-1391)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.lib.export_accounting import (
    compact_accounting,
    export_diagnostics,
    diagnostics_sha256,
    export_details_reference,
    projection_summary,
)
from src.lib.flows.output_projection import (
    FlowOutputArtifact,
    FlowOutputArtifactBundle,
    FlowOutputField,
    FlowOutputProjectionPlan,
    apply_projection_plan,
    finalize_output_projection,
)
from src.lib.openai_agents.tools.output_formatter_tools import (
    build_output_formatter_tools,
)
from src.lib.openai_agents.streaming_tools import _formatter_saved_file_handoff
from src.lib.openai_agents import export_details_tool


def bundle(count=27, removed=15):
    rows = [
        {
            "object.object_id": f"r{i}",
            "object.attribute.kind": "exclude" if i < removed else "keep",
            "object.attribute.name": f"Name {i}",
            "artifact.node_id": "source",
            "artifact.is_canonical_curation_data": True,
        }
        for i in range(count)
    ]
    return FlowOutputArtifactBundle(
        flow_name="Export",
        artifacts=[
            FlowOutputArtifact(
                node_id="source", source_key="source", rows_by_source={"object": rows}
            )
        ],
        field_catalog=[
            FlowOutputField(
                ref=f"object.attribute.{key}",
                label=key,
                value_type="string",
                row_source="object",
            )
            for key in ("kind", "name")
        ],
        default_row_source="object",
    )


def plan(format="csv", **kwargs):
    return FlowOutputProjectionPlan.model_validate(
        {
            "format": format,
            "row_source": "object",
            "row_strategy": "wide_union",
            "columns": [{"key": "name", "field_ref": "object.attribute.name"}],
            "filters": [
                {"field_ref": "object.attribute.kind", "op": "ne", "value": "exclude"}
            ],
            **kwargs,
        }
    )


@pytest.mark.parametrize("count,removed", [(27, 15), (57, 10), (5, 0), (5, 5)])
@pytest.mark.parametrize("format", ["csv", "tsv", "json"])
def test_exact_filter_stages_preserve_rows(count, removed, format):
    result = finalize_output_projection(bundle(count, removed), plan(format))
    facts = result.export_accounting
    assert (
        facts.candidate_row_count,
        facts.rows_removed_by_filters,
        facts.rows_removed_by_overrides,
        facts.matched_row_count,
        facts.exported_row_count,
    ) == (count, removed, 0, count - removed, count - removed)
    assert facts.row_unit == "object" and not facts.truncated
    assert result.rows == [{"name": f"Name {i}"} for i in range(removed, count)]
    assert result.applied_filters[0]["rows_removed_sequentially"] == removed
    assert projection_summary(result)["row_count"] == count - removed


def test_overlapping_filters_preview_and_explicit_limit_are_distinct():
    p = plan(max_rows=3)
    p.filters.append(p.filters[0].model_copy())
    result = apply_projection_plan(bundle(), p, preview_limit=2)
    assert [f["rows_removed_sequentially"] for f in result.applied_filters] == [15, 0]
    assert result.export_accounting.matched_row_count == 12
    assert result.export_accounting.exported_row_count == 2
    assert result.export_accounting.limited_by_max_rows
    final = finalize_output_projection(bundle(), p)
    assert final.export_accounting.exported_row_count == 3
    assert projection_summary(final)["row_count"] == 3
    assert projection_summary(final)["total_count"] == 12


def test_grouped_json_counts_rows_separately_from_groups():
    result = finalize_output_projection(
        bundle(5, 0),
        plan("json", group_by=["object.attribute.kind"], json_shape="grouped"),
    )
    assert result.export_accounting.group_count == 1
    assert result.export_accounting.exported_row_count == 5
    assert len(result.json_data) == 1 and len(result.json_data[0]["rows"]) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("count,removed,success", [(5, 5, True), (0, 0, False)])
async def test_intentional_empty_export_vs_absent_source(count, removed, success):
    saves = []

    async def save(*args):
        saves.append(args)
        return {
            "file_id": str(uuid4()),
            "filename": "rows.csv",
            "download_url": "/api/files/example/download",
        }

    tools = build_output_formatter_tools(
        bundle=bundle(count, removed),
        output_format="csv",
        formatter_agent_id="formatter",
        save_projected_output=save,
    )
    finalizer = next(t for t in tools if t.name == "finalize_and_save")
    payload = json.loads(
        await finalizer.on_invoke_tool(
            SimpleNamespace(tool_name=finalizer.name),
            json.dumps({"plan_json": plan().model_dump_json()}),
        )
    )
    assert (payload["status"] == "ok") == success
    assert len(saves) == int(success)
    if success:
        assert payload["export_accounting"]["counts"]["exported_row_count"] == 0
        duplicate = json.loads(
            await finalizer.on_invoke_tool(
                SimpleNamespace(tool_name=finalizer.name), "{}"
            )
        )
        assert (
            _formatter_saved_file_handoff(duplicate)["export_accounting"]
            == payload["export_accounting"]
        )
        assert len(saves) == 1


def saved_receipt(result):
    fid = str(uuid4())
    return {
        "status": "ok",
        "file_id": fid,
        "filename": "rows.csv",
        "download_url": f"/api/files/{fid}/download",
        "export_accounting": compact_accounting(result),
        "export_details": export_details_reference(fid, result),
    }


def test_handoff_whitelists_counts_and_preserves_warning_coverage(monkeypatch):
    monkeypatch.setenv("FLOW_PROJECTION_MAX_LIST_ITEMS", "2")
    monkeypatch.setenv("FLOW_PROJECTION_MAX_TEXT_CHARS", "10")
    b = bundle()
    b.warnings = ["α" * 30, "second", "third"]
    result = finalize_output_projection(b, plan())
    payload = saved_receipt(result)
    payload["secret"] = "private"
    receipt = _formatter_saved_file_handoff(payload)
    assert receipt["export_accounting"]["counts"]["warning_count"] == 3
    assert receipt["export_accounting"]["warnings_omitted"] == 1
    assert receipt["export_accounting"]["warning_text_truncated"]
    assert "private" not in json.dumps(receipt)
    assert export_diagnostics(result)["warnings"] == b.warnings
    payload["export_accounting"]["counts"]["exported_row_count"] = 999
    invalid = _formatter_saved_file_handoff(payload)
    assert invalid["saved_file"] is True
    assert invalid["export_accounting"]["status"] == "unavailable"
    assert "counts" not in invalid["export_accounting"]


def setup_read(monkeypatch, diagnostics):
    db = MagicMock()
    db.__enter__.return_value = db
    db.query.return_value.filter.return_value.one_or_none.return_value = (
        SimpleNamespace(file_metadata={"export_diagnostics": diagnostics})
    )
    monkeypatch.setattr(export_details_tool, "SessionLocal", lambda: db)
    monkeypatch.setattr(export_details_tool, "get_current_user_id", lambda: "owner")
    monkeypatch.setattr(
        export_details_tool, "get_current_session_id", lambda: "session"
    )
    return db


def test_exact_diagnostics_roundtrip_scope_and_stale_hash(monkeypatch):
    result = finalize_output_projection(bundle(), plan())
    result.warnings = ["α" * 1200]
    diagnostics = export_diagnostics(result)
    db = setup_read(monkeypatch, diagnostics)
    fid = str(uuid4())
    digest = diagnostics_sha256(diagnostics)
    offset = 0
    chunks = []
    while True:
        out = export_details_tool.read_export_details_payload(
            fid, digest, offset, max_chars=71
        )
        assert out["status"] == "ok"
        chunks.append(out["text"])
        if out["next_offset"] is None:
            break
        offset = out["next_offset"]
    assert json.loads("".join(chunks)) == diagnostics
    filters = db.query.return_value.filter.call_args.args
    assert len(filters) == 3
    assert {str(f.left) for f in filters} == {
        "file_outputs.id",
        "file_outputs.curator_id",
        "file_outputs.session_id",
    }
    assert (
        export_details_tool.read_export_details_payload(fid, "0" * 64)["status"]
        == "stale"
    )
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    assert (
        export_details_tool.read_export_details_payload(fid, digest)["status"]
        == "unavailable"
    )
    monkeypatch.setattr(export_details_tool, "get_current_user_id", lambda: None)
    db.reset_mock()
    assert (
        export_details_tool.read_export_details_payload(fid, digest)["status"]
        == "unavailable"
    )
    db.query.assert_not_called()


def test_read_failure_reports_once_without_sensitive_content(monkeypatch):
    db = setup_read(monkeypatch, {})
    db.query.side_effect = RuntimeError("password=secret curator document text")
    reports = []
    monkeypatch.setattr(
        export_details_tool,
        "report_runtime_exception",
        lambda *a, **k: reports.append((a, k)),
    )
    out = export_details_tool.read_export_details_payload(str(uuid4()), "0" * 64)
    assert out["status"] == "error" and len(reports) == 1
    assert "secret" not in str(reports) and "document text" not in str(reports)
    assert "secret" not in str(out)


def test_override_exclusions_are_separate_from_filter_removals():
    from src.lib.flows.output_projection import bundle_row_refs, FlowOutputOverrideSpec

    source = bundle(5, 2)
    refs = bundle_row_refs(source, "object")
    p = plan()
    p.overrides = [
        FlowOutputOverrideSpec(
            row_ref=refs[id(source.rows_for_source("object")[3])], exclude=True
        )
    ]
    result = finalize_output_projection(source, p)
    assert result.export_accounting.rows_removed_by_filters == 2
    assert result.export_accounting.rows_removed_by_overrides == 1
    assert result.export_accounting.exported_row_count == 2


@pytest.mark.asyncio
async def test_parallel_formatter_receipts_keep_counts_and_warnings_isolated():
    import asyncio

    async def run(count, removed, warning):
        source = bundle(count, removed)
        source.warnings = [warning]

        async def save(*args):
            await asyncio.sleep(0)
            fid = str(uuid4())
            return {
                "file_id": fid,
                "filename": "rows.csv",
                "download_url": f"/api/files/{fid}/download",
            }

        tools = build_output_formatter_tools(
            bundle=source,
            output_format="csv",
            formatter_agent_id="formatter",
            save_projected_output=save,
        )
        tool = next(t for t in tools if t.name == "finalize_and_save")
        return json.loads(
            await tool.on_invoke_tool(
                SimpleNamespace(tool_name=tool.name),
                json.dumps({"plan_json": plan().model_dump_json()}),
            )
        )

    first, second = await asyncio.gather(run(27, 15, "first"), run(57, 10, "second"))
    assert first["file_id"] != second["file_id"]
    for payload, count, warning in [(first, 12, "first"), (second, 47, "second")]:
        receipt = _formatter_saved_file_handoff(payload)
        assert receipt["export_accounting"]["counts"]["exported_row_count"] == count
        assert receipt["export_accounting"]["warnings"] == [warning]
        assert receipt["export_details"]["read"]["file_id"] == payload["file_id"]
