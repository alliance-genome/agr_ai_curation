"""Unit tests for Agent Studio catalog endpoints."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class TestAgentStudioCatalogEndpoints:
    def test_get_catalog_returns_catalog_response(self, monkeypatch):
        import src.api.agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog

        service = SimpleNamespace(catalog=PromptCatalog(total_agents=1, available_mods=["WB"]))
        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: service)
        observed = {"called": False}

        def _merge(catalog, _user, _db):
            observed["called"] = True
            return catalog

        monkeypatch.setattr(api_module, "_merge_custom_agents_into_catalog", _merge)

        result = asyncio.run(
            api_module.get_catalog(
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )

        assert observed["called"] is True
        assert result.catalog.total_agents == 1
        assert result.catalog.available_mods == ["WB"]

    def test_get_catalog_maps_unexpected_errors_to_500(self, monkeypatch):
        import src.api.agent_studio as api_module

        monkeypatch.setattr(
            api_module,
            "get_prompt_catalog",
            lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(api_module.get_catalog(user={"sub": "auth-sub"}, db=SimpleNamespace()))

        assert exc_info.value.status_code == 500
        assert "catalog unavailable" in str(exc_info.value.detail)

    def test_refresh_catalog_calls_refresh_and_returns_catalog(self, monkeypatch):
        import src.api.agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog

        refreshed = {"value": False}

        def _refresh():
            refreshed["value"] = True

        service = SimpleNamespace(
            catalog=PromptCatalog(total_agents=2, available_mods=["WB", "RGD"]),
            refresh=_refresh,
        )
        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: service)
        observed = {"merge_called": False}

        def _merge(catalog, _user, _db):
            observed["merge_called"] = True
            return catalog

        monkeypatch.setattr(api_module, "_merge_custom_agents_into_catalog", _merge)

        result = asyncio.run(
            api_module.refresh_catalog(
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )

        assert refreshed["value"] is True
        assert observed["merge_called"] is True
        assert result.catalog.total_agents == 2

    def test_get_combined_prompt_success_and_404(self, monkeypatch):
        import src.api.agent_studio as api_module

        service = SimpleNamespace(get_combined_prompt=lambda agent_id, mod_id: f"{agent_id}-{mod_id}-prompt")
        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: service)

        success = asyncio.run(
            api_module.get_combined_prompt(
                request=api_module.CombinedPromptRequest(agent_id="gene", mod_id="WB"),
                user={"sub": "auth-sub"},
            )
        )
        assert success.combined_prompt == "gene-WB-prompt"

        monkeypatch.setattr(
            api_module,
            "get_prompt_catalog",
            lambda: SimpleNamespace(get_combined_prompt=lambda *_args, **_kwargs: None),
        )
        with pytest.raises(HTTPException) as not_found_exc:
            asyncio.run(
                api_module.get_combined_prompt(
                    request=api_module.CombinedPromptRequest(agent_id="gene", mod_id="WB"),
                    user={"sub": "auth-sub"},
                )
            )
        assert not_found_exc.value.status_code == 404

    def test_get_combined_prompt_maps_unexpected_errors_to_500(self, monkeypatch):
        import src.api.agent_studio as api_module

        service = SimpleNamespace(
            get_combined_prompt=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: service)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.get_combined_prompt(
                    request=api_module.CombinedPromptRequest(agent_id="gene", mod_id="WB"),
                    user={"sub": "auth-sub"},
                )
            )
        assert exc_info.value.status_code == 500
        assert "boom" in str(exc_info.value.detail)
