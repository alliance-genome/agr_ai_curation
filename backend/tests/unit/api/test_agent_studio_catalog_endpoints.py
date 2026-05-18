"""Unit tests for Agent Studio catalog endpoints."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class TestAgentStudioCatalogEndpoints:
    def test_get_catalog_returns_catalog_response(self, monkeypatch):
        import src.api.agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog

        service = SimpleNamespace(catalog=PromptCatalog(total_agents=1, available_groups=["WB"]))
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
        assert result.catalog.available_groups == ["WB"]

    def test_get_catalog_maps_unexpected_errors_to_500(self, monkeypatch, caplog):
        import src.api.agent_studio as api_module

        caplog.set_level(logging.ERROR, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "get_prompt_catalog",
            lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(api_module.get_catalog(user={"sub": "auth-sub"}, db=SimpleNamespace()))

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Failed to load prompt catalog"
        assert "catalog unavailable" not in str(exc_info.value.detail)
        assert "catalog unavailable" in caplog.text

    def test_refresh_catalog_calls_refresh_and_returns_catalog(self, monkeypatch):
        import src.api.agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog

        refreshed = {"value": False}

        def _refresh():
            refreshed["value"] = True

        service = SimpleNamespace(
            catalog=PromptCatalog(total_agents=2, available_groups=["WB", "RGD"]),
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

    def test_refresh_catalog_maps_unexpected_errors_to_500(self, monkeypatch, caplog):
        import src.api.agent_studio as api_module

        caplog.set_level(logging.ERROR, logger=api_module.logger.name)

        def _refresh():
            raise RuntimeError("refresh unavailable")

        monkeypatch.setattr(
            api_module,
            "get_prompt_catalog",
            lambda: SimpleNamespace(refresh=_refresh, catalog=SimpleNamespace()),
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.refresh_catalog(
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Failed to refresh prompt catalog"
        assert "refresh unavailable" not in str(exc_info.value.detail)
        assert "refresh unavailable" in caplog.text

    def test_get_combined_prompt_success_and_404(self, monkeypatch):
        import src.api.agent_studio as api_module

        bundle = SimpleNamespace(
            render=lambda: "gene-WB-prompt",
            hash="hash-1",
            to_manifest=lambda: {"agent_id": "gene", "layers": [], "hash": "hash-1"},
        )
        service = SimpleNamespace(get_effective_prompt_bundle=lambda agent_id, group_id: bundle)
        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: service)

        success = asyncio.run(
            api_module.get_combined_prompt(
                request=api_module.CombinedPromptRequest(agent_id="gene", group_id="WB"),
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )
        assert success.combined_prompt == "gene-WB-prompt"
        assert success.group_id == "WB"
        assert success.effective_prompt_hash == "hash-1"

        monkeypatch.setattr(
            api_module,
            "get_prompt_catalog",
            lambda: SimpleNamespace(get_effective_prompt_bundle=lambda *_args, **_kwargs: None),
        )
        with pytest.raises(HTTPException) as not_found_exc:
            asyncio.run(
                api_module.get_combined_prompt(
                    request=api_module.CombinedPromptRequest(agent_id="gene", group_id="WB"),
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )
        assert not_found_exc.value.status_code == 404

    def test_get_combined_prompt_supports_custom_agents(self, monkeypatch):
        import src.api.agent_studio as api_module

        fake_custom = SimpleNamespace(
            parent_agent_key="gene",
            custom_prompt="Curator overlay",
            group_prompt_overrides={"WB": "Custom WB overlay"},
            group_rules_enabled=True,
        )
        observed = {}

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_visible_to_user",
            lambda _db, _uuid, _uid: fake_custom,
        )
        monkeypatch.setattr(
            api_module,
            "normalize_custom_overlay_for_parent",
            lambda *_args, **_kwargs: SimpleNamespace(
                content="Curator overlay",
                status="clean",
                removed_layer_kinds=[],
                warning=None,
            ),
        )

        def _build_agent_prompt_layers(agent_id, **kwargs):
            observed["agent_id"] = agent_id
            observed["group_id"] = kwargs.get("group_id")
            observed["overlay"] = kwargs.get("overlay")
            return SimpleNamespace(
                render=lambda: (
                    "Locked core\n\nParent base\n\nWB rules\n\n"
                    "Curator overlay\n\nCustom WB overlay"
                ),
                hash="hash-custom-wb",
                to_manifest=lambda: {
                    "agent_id": "gene",
                    "hash": "hash-custom-wb",
                    "layers": [
                        {"kind": "core_static", "locked": True, "editable": False},
                        {"kind": "core_generated", "locked": True, "editable": False},
                        {"kind": "group_rules", "locked": False, "editable": True},
                        {"kind": "curator_overlay", "locked": False, "editable": True},
                    ],
                },
            )

        monkeypatch.setattr(api_module, "build_agent_prompt_layers", _build_agent_prompt_layers)

        result = asyncio.run(
            api_module.get_combined_prompt(
                request=api_module.CombinedPromptRequest(
                    agent_id="ca_11111111-2222-3333-4444-555555555555",
                    group_id="WB",
                ),
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )

        assert result.combined_prompt == (
            "Locked core\n\nParent base\n\nWB rules\n\nCurator overlay\n\nCustom WB overlay"
        )
        assert result.effective_prompt_hash == "hash-custom-wb"
        assert observed == {
            "agent_id": "gene",
            "group_id": "WB",
            "overlay": "Curator overlay\n\n## Curator group overlay: WB\nCustom WB overlay",
        }
        assert result.layer_manifest["layers"][0]["locked"] is True

    def test_get_combined_prompt_maps_unexpected_errors_to_500(self, monkeypatch, caplog):
        import src.api.agent_studio as api_module

        caplog.set_level(logging.ERROR, logger=api_module.logger.name)
        service = SimpleNamespace(
            get_effective_prompt_bundle=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: service)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.get_combined_prompt(
                    request=api_module.CombinedPromptRequest(agent_id="gene", mod_id="WB"),
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Failed to get combined prompt"
        assert "boom" not in str(exc_info.value.detail)
        assert "boom" in caplog.text

    def test_combined_prompt_request_accepts_legacy_mod_id_alias(self):
        import src.api.agent_studio as api_module

        request = api_module.CombinedPromptRequest(agent_id="gene", mod_id="WB")

        assert request.group_id == "WB"
