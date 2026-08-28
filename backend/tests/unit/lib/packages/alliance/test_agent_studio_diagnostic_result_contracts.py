"""Provider-boundary coverage for Alliance package diagnostics."""

from __future__ import annotations

import hashlib
import json

import pytest
import sqlalchemy as sa

from src.lib.agent_studio.diagnostic_tools import result_contracts


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _provider_visible(tool_name: str, result: dict) -> dict:
    from src.api import agent_studio

    content = agent_studio._provider_tool_result_content(
        tool_name=tool_name,
        tool_input={},
        tool_result=result,
        session_id="diagnostic-session",
        turn_id="diagnostic-turn",
    )
    assert len(content) <= 12_000
    provider_result = json.loads(content)
    assert provider_result.get("status") != "compacted_tool_result"
    return provider_result


@pytest.fixture(autouse=True)
def _diagnostic_limits(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_RESULT_MAX_CHARS", "3000")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_CHUNK_MAX_CHARS", "1000")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_SCALAR_PREVIEW_MAX_CHARS", "80")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_PAGE_DEFAULT_ITEMS", "10")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_PAGE_MAX_ITEMS", "25")


@pytest.mark.parametrize(
    ("tool_id", "result", "page_path"),
    [
        (
            "agr_curation_query",
            {
                "status": "ok",
                "count": 500,
                "data": [
                    {"id": f"RGD:{index}", "provenance": "curation:" + "x" * 500}
                    for index in range(500)
                ],
                "lookup_attempts": [],
                "candidate_matches": [],
                "result_projections": [],
            },
            "data",
        ),
        (
            "go_api_call",
            {
                "status": "ok",
                "gene_id": "RGD:620474",
                "source_cursor": 0,
                "source_limit": 500,
                "returned_count": 500,
                "next_source_cursor": 500,
                "source_complete": False,
                "source_limit_capped": False,
                "source_response_truncated": False,
                "annotations": [
                    {
                        "go_id": f"GO:{index:07d}",
                        "references": [f"PMID:{index}"],
                        "provenance": {"source_record_id": "record-" + "y" * 500},
                    }
                    for index in range(500)
                ],
            },
            "annotations",
        ),
        (
            "resolve_gene_product",
            {
                "status": "ambiguous",
                "query": "rno-miR-124-3p",
                "candidate_limit_reached": True,
                "product_candidates": [],
                "candidate_mappings": [
                    {
                        "gene_id": f"RGD:{index}",
                        "provenance": [
                            {"source_record_id": f"record-{index}", "evidence": "z" * 500}
                        ],
                    }
                    for index in range(25)
                ],
                "provenance": [
                    {"source_record_id": f"record-{index}", "evidence": "z" * 500}
                    for index in range(25)
                ],
                "message": "Multiple evidence-backed candidates remain unresolved",
            },
            "candidate_mappings",
        ),
    ],
)
def test_structured_maximum_results_are_summary_page_and_detail_addressable(
    tool_id,
    result,
    page_path,
):
    contract = {
        "kind": "structured",
        "page_paths": {
            "agr_curation_query": [
                "data",
                "lookup_attempts",
                "candidate_matches",
                "result_projections",
            ],
            "go_api_call": ["annotations"],
            "resolve_gene_product": [
                "product_candidates",
                "candidate_mappings",
                "provenance",
            ],
        }[tool_id],
    }
    handler = result_contracts.create_bounded_result_handler(lambda: result, contract)

    summary = handler()
    page = handler(result_view="page", result_path=page_path, result_limit=25)

    assert summary["result_view"] == "summary"
    assert summary["fields"][page_path]["count"] == len(result[page_path])
    assert page["result_view"] == "page"
    assert page["total_count"] == len(result[page_path])
    assert page["returned_count"] > 0
    assert page["truncated"] is (page["next_cursor"] is not None)
    _provider_visible(tool_id, summary)
    _provider_visible(tool_id, page)

    first = page["items"][0]
    if "detail_path" in first:
        detail = handler(
            result_view="detail",
            detail_path=first["detail_path"],
            detail_max_chars=1000,
        )
        expected_item = result[page_path][0]
        assert detail["sha256"] == hashlib.sha256(
            _canonical(expected_item).encode("utf-8")
        ).hexdigest()
        assert detail["complete"] is False
        _provider_visible(tool_id, detail)


@pytest.mark.parametrize(
    ("tool_id", "result", "detail_path"),
    [
        (
            "chebi_api_call",
            {"status": "ok", "status_code": 200, "data": {"results": ["CHEBI:" + "x" * 400] * 200}},
            "data",
        ),
        (
            "quickgo_api_call",
            {"status": "error", "status_code": 502, "message": "HTTP 502: " + "upstream-error-" * 2000},
            "message",
        ),
    ],
)
def test_rest_success_and_error_bodies_reconstruct_from_exact_chunks(
    tool_id,
    result,
    detail_path,
):
    handler = result_contracts.create_bounded_result_handler(
        lambda: result,
        {"kind": "raw", "page_paths": []},
    )
    summary = handler()
    expected = result[detail_path] if isinstance(result[detail_path], str) else _canonical(result[detail_path])
    chunks = []
    cursor = 0
    while True:
        detail = handler(
            result_view="detail",
            detail_path=detail_path,
            detail_cursor=cursor,
            detail_max_chars=1000,
        )
        chunks.append(detail["content"])
        _provider_visible(tool_id, detail)
        if detail["complete"]:
            break
        cursor = detail["next_cursor"]

    assert summary["status"] == result["status"]
    assert summary["fields"][detail_path]["sha256"] == hashlib.sha256(
        expected.encode("utf-8")
    ).hexdigest()
    assert "".join(chunks) == expected
    _provider_visible(tool_id, summary)


@pytest.mark.parametrize(
    "payload",
    [
        "\0" * 6_000,
        '\\"' * 3_000,
        "é" * 2_500,
    ],
    ids=["escaped-controls", "slashes-and-quotes", "non-ascii"],
)
def test_chunks_budget_against_provider_json_serialization(monkeypatch, payload):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "1000")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_RESULT_MAX_CHARS", "20000")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_CHUNK_MAX_CHARS", "6000")
    handler = result_contracts.create_bounded_result_handler(
        lambda: {"status": "ok", "message": payload},
        {"kind": "raw", "page_paths": []},
    )

    chunks = []
    cursor = 0
    while True:
        detail = handler(
            result_view="detail",
            detail_path="message",
            detail_cursor=cursor,
        )
        content = json.dumps(detail, default=str)
        assert len(content) <= 1000
        provider_result = _provider_visible("quickgo_api_call", detail)
        chunks.append(provider_result["content"])
        if detail["complete"]:
            break
        assert detail["next_cursor"] > cursor
        cursor = detail["next_cursor"]

    assert "".join(chunks) == payload


@pytest.mark.parametrize("payload", ["\0" * 2_500, '\\"' * 2_500, "é" * 2_500])
def test_structured_pages_budget_escaped_items_for_provider(payload):
    handler = result_contracts.create_bounded_result_handler(
        lambda: {"status": "ok", "data": [{"payload": payload}]},
        {"kind": "structured", "page_paths": ["data"]},
    )

    page = handler(result_view="page", result_path="data")

    assert page["returned_count"] == 1
    assert page["items"][0]["detail_path"] == "data.0"
    _provider_visible("agr_curation_query", page)


def test_structured_summary_drops_previews_to_stay_within_provider_budget(
    monkeypatch,
):
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_RESULT_MAX_CHARS", "1400")
    result = {
        "status": "ok",
        **{f"scalar_{index}": f"value-{index}-" + "x" * 500 for index in range(4)},
    }
    handler = result_contracts.create_bounded_result_handler(
        lambda: result,
        {"kind": "structured", "page_paths": []},
    )

    summary = handler()

    assert len(json.dumps(summary, default=str)) <= 1400
    assert any(
        "preview" not in summary["fields"][f"scalar_{index}"]
        for index in range(4)
    )
    assert all(
        summary["fields"][f"scalar_{index}"]["detail_path"]
        == f"@field.{index}.value"
        for index in range(4)
    )
    _provider_visible("agr_curation_query", summary)


def test_many_field_summary_metadata_is_bounded_and_exactly_recoverable(
    monkeypatch,
):
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_RESULT_MAX_CHARS", "1400")
    oversized_name = "field.name." + "n" * 2000
    result = {
        "status": "ok",
        oversized_name: "oversized-name-value-" + "v" * 500,
        **{f"scalar_{index:03d}": f"value-{index}-" + "x" * 500 for index in range(100)},
    }
    handler = result_contracts.create_bounded_result_handler(
        lambda: result,
        {"kind": "structured", "page_paths": []},
    )

    recovered = {}
    cursor = 0
    while True:
        summary = handler(result_view="summary", result_cursor=cursor)
        assert len(json.dumps(summary, default=str)) <= 1400
        _provider_visible("agr_curation_query", summary)
        assert summary["returned_field_count"] > 0

        for name, descriptor in summary["fields"].items():
            if name.startswith("@field."):
                metadata_chunks = []
                detail_cursor = 0
                while True:
                    detail = handler(
                        result_view="detail",
                        detail_path=descriptor["metadata_detail_path"],
                        detail_cursor=detail_cursor,
                    )
                    assert len(json.dumps(detail, default=str)) <= 1400
                    metadata_chunks.append(detail["content"])
                    if detail["complete"]:
                        break
                    detail_cursor = detail["next_cursor"]
                metadata_content = "".join(metadata_chunks)
                assert hashlib.sha256(metadata_content.encode()).hexdigest() == descriptor[
                    "metadata_sha256"
                ]
                metadata = json.loads(metadata_content)
                name = metadata["name"]
                descriptor = {"detail_path": metadata["value_detail_path"]}

            if isinstance(descriptor, dict) and "detail_path" in descriptor:
                value_chunks = []
                detail_cursor = 0
                while True:
                    detail = handler(
                        result_view="detail",
                        detail_path=descriptor["detail_path"],
                        detail_cursor=detail_cursor,
                    )
                    assert len(json.dumps(detail, default=str)) <= 1400
                    value_chunks.append(detail["content"])
                    if detail["complete"]:
                        break
                    detail_cursor = detail["next_cursor"]
                recovered[name] = "".join(value_chunks)
            else:
                recovered[name] = descriptor

        if summary["fields_complete"]:
            break
        assert summary["next_field_cursor"] > cursor
        cursor = summary["next_field_cursor"]

    assert recovered == result


def test_sql_diagnostic_counts_pages_and_chunks_without_full_materialization(monkeypatch):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE records (id INTEGER, payload TEXT)"))
        connection.execute(
            sa.text("INSERT INTO records (id, payload) VALUES (:id, :payload)"),
            [{"id": index, "payload": f"row-{index}-" + "p" * 4000} for index in range(60)],
        )
    executed = []

    @sa.event.listens_for(engine, "before_cursor_execute")
    def _record_sql(_connection, _cursor, statement, parameters, _context, _many):
        executed.append((statement, parameters))

    monkeypatch.setattr(result_contracts.sa, "create_engine", lambda _url: engine)
    handler = result_contracts.create_sql_query_handler(
        "sqlite://unused",
        {"kind": "sql_rows", "page_paths": ["rows"]},
    )

    summary = handler(query="SELECT id, payload FROM records ORDER BY id")
    page = handler(
        query="SELECT id, payload FROM records ORDER BY id",
        result_view="page",
        result_path="rows",
        result_cursor=20,
        result_limit=10,
    )
    detail = handler(
        query="SELECT id, payload FROM records ORDER BY id",
        result_view="detail",
        detail_path="rows.20.payload",
        detail_max_chars=1000,
    )
    rejected = handler(query="DELETE FROM records")
    executed_before_invalid_cursor = list(executed)
    invalid_cursors = [
        handler(
            query="SELECT id FROM records",
            result_view="page",
            result_path="rows",
            result_cursor=value,
        )
        for value in ("0; SELECT 999", True, -1)
    ]

    assert summary["fields"]["count"] == 60
    assert page["total_count"] == 60
    assert page["cursor"] == 20
    assert page["next_cursor"] > 20
    assert page["truncated"] is True
    assert detail["content"].startswith("row-20-")
    assert detail["complete"] is False
    assert rejected["status"] == "error"
    assert "Only SELECT" in rejected["message"]
    assert all(result["status"] == "error" for result in invalid_cursors)
    assert all("non-negative integer" in result["message"] for result in invalid_cursors)
    assert executed == executed_before_invalid_cursor
    assert any(
        "LIMIT ? OFFSET ?" in statement and parameters == (11, 20)
        for statement, parameters in executed
    )
    assert not any("DELETE FROM records" in statement for statement, _ in executed)
    _provider_visible("curation_db_sql", summary)
    _provider_visible("curation_db_sql", page)
    _provider_visible("curation_db_sql", detail)


def test_all_installed_alliance_diagnostics_declare_continuation_contracts():
    from src.lib.agent_studio import catalog_service

    catalog = catalog_service.get_tool_registry()
    expected = {
        "agr_curation_query": ("structured", {"data", "lookup_attempts", "candidate_matches", "result_projections"}),
        "curation_db_sql": ("sql_rows", {"rows"}),
        "chebi_api_call": ("raw", set()),
        "quickgo_api_call": ("raw", set()),
        "go_api_call": ("structured", {"annotations"}),
        "resolve_gene_product": ("structured", {"product_candidates", "candidate_mappings", "provenance"}),
    }

    for tool_id, (kind, page_paths) in expected.items():
        diagnostic = catalog[tool_id]["agent_studio"]["diagnostic"]
        assert diagnostic["result_contract"]["kind"] == kind
        assert set(diagnostic["result_contract"]["page_paths"]) == page_paths


def test_enabled_package_diagnostic_without_result_contract_fails_registration(monkeypatch):
    from types import SimpleNamespace

    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.agent_studio.diagnostic_tools.registry import DiagnosticToolRegistry

    binding = SimpleNamespace(tool_id="unsafe_diagnostic", required_context=())
    diagnostic = {
        "enabled": True,
        "description": "Unsafe diagnostic",
        "category": "demo",
        "tags": ["demo"],
        "input_schema": {"type": "object", "properties": {}},
    }
    monkeypatch.setattr(
        catalog_service,
        "get_tool_registry",
        lambda: {"unsafe_diagnostic": {"agent_studio": {"diagnostic": diagnostic}}},
    )
    monkeypatch.setattr(
        catalog_service,
        "_load_package_tool_registry",
        lambda: SimpleNamespace(bindings=[binding]),
    )
    monkeypatch.setattr(
        catalog_service,
        "_build_tool_execution_context",
        lambda _kwargs: SimpleNamespace(database_url=None),
    )

    with pytest.raises(ValueError, match="must declare.*result_contract"):
        tool_definitions._register_package_diagnostic_tools(DiagnosticToolRegistry())


def test_registered_rest_diagnostic_preserves_package_domain_allowlist(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.agent_studio.diagnostic_tools.registry import DiagnosticToolRegistry
    from agr_ai_curation_alliance.tools import rest_api

    monkeypatch.setattr(
        catalog_service,
        "_build_tool_execution_context",
        lambda _kwargs: type("Context", (), {"database_url": None})(),
    )
    monkeypatch.setattr(
        rest_api.requests,
        "request",
        lambda **_kwargs: pytest.fail("disallowed URL must not reach requests"),
    )
    registry = DiagnosticToolRegistry()
    tool_definitions._register_package_diagnostic_tools(registry)

    chebi_tool = registry.get_tool("chebi_api_call")
    assert chebi_tool is not None
    result = chebi_tool.handler(url="https://example.org/unbounded")

    assert result["status"] == "error", result
    assert "Domain not allowed" in result["fields"]["message"]
    _provider_visible("chebi_api_call", result)


def test_registered_schemas_advertise_bounded_continuation(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.agent_studio.diagnostic_tools.registry import DiagnosticToolRegistry

    monkeypatch.setattr(
        catalog_service,
        "_build_tool_execution_context",
        lambda _kwargs: type("Context", (), {"database_url": None})(),
    )
    registry = DiagnosticToolRegistry()
    tool_definitions._register_package_diagnostic_tools(registry)

    for tool_id in (
        "agr_curation_query",
        "chebi_api_call",
        "quickgo_api_call",
        "go_api_call",
        "resolve_gene_product",
    ):
        tool = registry.get_tool(tool_id)
        assert tool is not None
        properties = tool.input_schema["properties"]
        if tool_id in {"chebi_api_call", "quickgo_api_call"}:
            assert properties["result_view"]["enum"] == ["summary", "detail"]
        else:
            assert properties["result_view"]["enum"] == [
                "summary",
                "page",
                "detail",
            ]
        assert properties["result_limit"]["maximum"] == 25
        assert properties["result_cursor"]["minimum"] == 0
        assert properties["detail_max_chars"]["maximum"] == 1000
        assert "Result contract:" in tool.description


def test_package_diagnostic_limits_share_documented_environment(monkeypatch):
    from agr_ai_curation_alliance.tools import agr_curation, go_annotations
    from src.lib.openai_agents import config

    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_PAGE_DEFAULT_ITEMS", "4")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_PAGE_MAX_ITEMS", "7")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_RESULT_MAX_CHARS", "2222")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_CHUNK_MAX_CHARS", "1111")
    monkeypatch.setenv("AGENT_STUDIO_PACKAGE_DIAGNOSTIC_SCALAR_PREVIEW_MAX_CHARS", "33")
    monkeypatch.setenv("AGR_DEFAULT_LIMIT", "6")
    monkeypatch.setenv("AGR_HARD_MAX", "9")
    monkeypatch.setenv("GO_ANNOTATIONS_PAGE_MAX_RESULTS", "12")

    assert config.get_agent_studio_package_diagnostic_page_default_items() == 4
    assert config.get_agent_studio_package_diagnostic_page_max_items() == 7
    assert config.get_agent_studio_package_diagnostic_result_max_chars() == 2222
    assert config.get_agent_studio_package_diagnostic_chunk_max_chars() == 1111
    assert config.get_agent_studio_package_diagnostic_scalar_preview_max_chars() == 33
    assert agr_curation._normalize_limit(None) == (6, ["default_limit_applied:6"])
    assert agr_curation._normalize_limit(20) == (9, ["limit_capped_at:9"])
    assert go_annotations._page_max_results() == 12
