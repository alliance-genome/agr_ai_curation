"""Unit tests for chunking strategies endpoint."""

from fastapi import HTTPException

from src.api import strategies


async def test_get_chunking_strategies_endpoint_success(monkeypatch):
    monkeypatch.setattr(
        strategies,
        "CHUNKING_STRATEGIES",
        {
            "research": {"method": "title", "max_chars": 2000, "overlap": 200},
            "alt": {"method": "paragraph", "max_chars": 1000, "overlap": 100, "exclude_types": ["table"]},
        },
    )

    result = await strategies.get_chunking_strategies_endpoint()

    assert result["default"] == "research"
    assert result["total"] == 2
    names = {entry["name"] for entry in result["strategies"]}
    assert names == {"research", "alt"}
    research = next(entry for entry in result["strategies"] if entry["name"] == "research")
    assert research["is_default"] is True
    assert research["description"].startswith("Optimized for academic")


async def test_get_chunking_strategies_endpoint_raises_http_500_on_error(monkeypatch):
    monkeypatch.setattr(strategies, "CHUNKING_STRATEGIES", {"broken": {"max_chars": 1000}})

    try:
        await strategies.get_chunking_strategies_endpoint()
        raise AssertionError("Expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 500
        assert "Failed to retrieve chunking strategies" in exc.detail


def test_get_strategy_description_default_branch():
    assert strategies._get_strategy_description("other") == "Research chunking strategy"

