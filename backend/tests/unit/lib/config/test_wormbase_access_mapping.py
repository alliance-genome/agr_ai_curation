"""Real WormBase identity names must reach both source and agent access policy."""

from pathlib import Path

import pytest

from src.lib.agent_access import is_resource_access_allowed
from src.lib.config.groups_loader import load_groups, reset_cache
from src.lib.document_sources.access import build_document_source_request_context
from src.lib.document_sources.import_selection import source_artifact_is_authorized
from src.lib.document_sources.models import (
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_groups, expected_groups",
    [
        (["WBStaff"], ("WB",)),
        (["WormBaseCurator"], ("WB",)),
        (["WBStaff", "WormBaseCurator", "unknown"], ("WB",)),
        (["MGIStaff"], ("MGI",)),
        (["unknown"], ()),
    ],
)
async def test_wormbase_identity_access_contract(monkeypatch, provider_groups, expected_groups):
    monkeypatch.setattr(
        "src.lib.document_sources.access.renewable_dev_curator_auth_required",
        lambda: False,
    )
    reset_cache()
    try:
        load_groups(Path(__file__).resolve().parents[5] / "config/groups.yaml", force_reload=True)
        context = await build_document_source_request_context(
            request=None, user_claims={"cognito:groups": provider_groups}
        )
        assert context.authorized_group_ids == expected_groups
        source = SourceArtifact(
            provider="test", artifact_id="wb-restricted",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("WB",)),
        )
        expected_access = "WB" in expected_groups
        assert source_artifact_is_authorized(
            source, authorized_group_ids=context.authorized_group_ids
        ) is expected_access
        assert is_resource_access_allowed(
            visibility_allowed=True, allowed_group_ids=["WB"],
            active_group_ids=list(context.authorized_group_ids),
        ) is expected_access
        assert not is_resource_access_allowed(
            visibility_allowed=False, allowed_group_ids=["WB"],
            active_group_ids=list(context.authorized_group_ids),
        )
    finally:
        reset_cache()
