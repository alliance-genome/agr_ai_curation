"""v0.11.8: a restored record needs no workspace row exactly when materialization gives it none."""
from types import SimpleNamespace

import pytest

from src.lib.domain_packs import materialization
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.domain_pack_metadata import DomainPackObjectDefinition


def _pack(role):
    definition = DomainPackObjectDefinition.model_validate({
        "object_type": "Mention", "display_name": "Mention",
        "metadata": {"object_role": role} if role else {},
        "fields": [{"field_path": "label", "field_type": "string"}],
    })
    return SimpleNamespace(metadata=SimpleNamespace(object_definitions=[definition], metadata={}))


@pytest.mark.parametrize("role,closed_profile,pack_found,expected", [
    ("metadata_only", False, True, True),     # an allele mention or quote: no row, none needed
    ("curatable_unit", False, True, False),   # a row record must still have its row
    (None, False, True, False),
    ("metadata_only", True, True, False),     # every closed-profile object is a row
    ("metadata_only", False, False, False),   # unknown pack: never skip
])
def test_has_no_review_row_follows_the_materializer_rule(monkeypatch, role, closed_profile, pack_found, expected):
    pack = _pack(role)
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
                        lambda _: pack if pack_found else None)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.resolve_envelope_profile_validation",
                        lambda *args, **kwargs: object() if closed_profile else None)
    obj = CuratableObjectEnvelope(object_type="Mention", pending_ref_id="m1", payload={"label": "w"})
    envelope = DomainEnvelope(envelope_id="restore-row", domain_pack_id="fixture", extracted_objects=[obj])
    assert materialization.has_no_review_row(None, envelope, obj) is expected
    # The materializer itself uses the same rule.
    role_seen = materialization._review_row_role(obj, pack.metadata.object_definitions[0], pack.metadata,
                                                 closed_profile=closed_profile)
    assert (role_seen == "metadata_only") is (role == "metadata_only" and not closed_profile)
