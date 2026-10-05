from uuid import UUID, uuid4

from src.lib.flow_transfer.ids import FLOW_IMPORT_NAMESPACE, derived_agent_key, derived_id

from .support import ISSUER


def test_the_namespace_is_fixed():
    assert FLOW_IMPORT_NAMESPACE == UUID("6f1c3a52-9d0e-4b7a-8e21-5c4f0b9a7d13")


def test_ids_are_stable_and_separate_per_importer_kind_and_issuer():
    source = uuid4()
    first = derived_id("flow", source, export_issuer=ISSUER, importer_sub="a")
    assert first == derived_id("flow", source, export_issuer=ISSUER, importer_sub="a")
    assert first.version == 5
    others = {
        derived_id("flow", source, export_issuer=ISSUER, importer_sub="b"),
        derived_id("agent", source, export_issuer=ISSUER, importer_sub="a"),
        derived_id("flow", source, export_issuer="https://ai-curation.alliancegenome.org", importer_sub="a"),
    }
    assert first not in others and len(others) == 3


def test_an_agent_key_is_a_custom_agent_key_of_the_derived_id():
    source = uuid4()
    key = derived_agent_key(source, export_issuer=ISSUER, importer_sub="a")
    assert key == f"ca_{derived_id('agent', source, export_issuer=ISSUER, importer_sub='a')}"
