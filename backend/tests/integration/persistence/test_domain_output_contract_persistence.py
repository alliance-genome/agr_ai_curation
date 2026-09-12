"""Forward migration preserves old pins and constrains new builder identities."""

from copy import deepcopy
import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from src.lib.agent_studio.execution_revision_service import append_execution_revision, get_execution_revision
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.models.sql.agent import Agent
from src.models.sql.agent_execution_revision import AgentExecutionRevision
from src.schemas.agent_execution_revision import AgentOutputContract, DomainExtractionRef
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401
from tests.unit.lib.agent_studio.test_domain_output_contract import installed_builder  # noqa: F401


def test_corrected_bootstrap_can_baseline_builder_before_flow_pins(execution_db, installed_builder, monkeypatch):  # noqa: F811
    from src.lib.agent_studio import catalog_service, custom_agent_service
    from src.lib.agent_studio.execution_revision_service import baseline_current_execution_heads

    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    head.tool_ids = ["finalize_gene_extraction"]
    head.allowed_group_ids = ["FB"]
    monkeypatch.setattr(catalog_service, "_inherited_curation_definition_for_db_agent", lambda agent: installed_builder if agent.id == agent_id else None)
    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda _db, tools: list(tools))
    assert baseline_current_execution_heads(db) == 2
    original_id = head.execution_revision_id
    row, saved = get_execution_revision(db, agent_id, original_id, 1, active_group_ids=["FB"])
    assert saved.output_contract.domain_extraction_ref.agent_id == "gene_extractor"
    assert saved.output_contract.output_schema_key is None
    assert head.inherited_allowed_group_ids == ["FB"]
    with pytest.raises(ValueError, match="cannot widen"):
        custom_agent_service._validate_inherited_access_floor(head, [])
    original = deepcopy(row.snapshot)
    installed_builder.curation.domain_pack_id = "generic"
    assert baseline_current_execution_heads(db) == 0
    assert head.execution_revision_id == original_id and row.snapshot == original


@pytest.fixture(params=["earlier-bootstrap", "corrected-bootstrap"])
def builder_db(execution_db, request):  # noqa: F811
    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    old = append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    old_bytes, old_fingerprint = deepcopy(old.snapshot), old.fingerprint
    path = Path(__file__).resolve().parents[3] / "alembic/versions/m0b1c2d3e4f5_packaged_builder_output_contract.py"
    spec = importlib.util.spec_from_file_location("builder_output_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(db.connection())):
        if request.param == "earlier-bootstrap":
            migration.downgrade()  # Reproduce the earlier, schema-only g4 constraints.
        migration.upgrade()
    db.refresh(old)
    assert (old.snapshot, old.fingerprint) == (old_bytes, old_fingerprint)
    _, read = get_execution_revision(db, agent_id, old.id, 1, active_group_ids=[])
    assert read.fingerprint() == old_fingerprint
    return db, head, old, saved, migration


def builder_snapshot(saved):
    result = deepcopy(saved)
    result.output_contract = AgentOutputContract(
        output_state="structured_extraction", output_mode="domain",
        domain_extraction_ref=DomainExtractionRef(package_id="agr.alliance", agent_id="gene_extractor", domain_pack_id="agr.alliance.gene"),
    )
    result.curation = {"adapter_key": "gene", "domain_pack_id": "agr.alliance.gene", "launchable": True}
    return result


def test_migration_preserves_old_receipts_and_round_trips_new_builder(builder_db):
    db, head, old, saved, migration = builder_db
    selected = builder_snapshot(saved)
    row = append_execution_revision(db, head, selected, user_id=1, expected_revision_id=old.id)
    _, restored = get_execution_revision(db, head.id, row.id, 1, active_group_ids=[])
    assert restored == selected
    assert row.output_schema_key is None
    assert row.profile_revision_id is None
    assert restored.fingerprint() == row.fingerprint
    with pytest.raises(DBAPIError, match="Cannot downgrade"):
        with db.begin_nested():
            with Operations.context(MigrationContext.configure(db.connection())):
                migration.downgrade()
    assert db.get(AgentExecutionRevision, row.id).snapshot == selected.model_dump(mode="json")


def test_save_preserves_new_package_access_floor_on_editable_head(builder_db, monkeypatch):
    from src.lib.agent_studio import custom_agent_service, execution_snapshot

    db, head, old, saved, _ = builder_db
    selected = builder_snapshot(saved)
    selected.allowed_group_ids = ["FB"]
    selected.inherited_allowed_group_ids = ["FB"]
    head.allowed_group_ids = ["FB"]
    assert head.inherited_allowed_group_ids == []
    def capture(_db, _agent, output, *, active_group_ids):
        assert output == selected.output_contract
        assert active_group_ids == ["FB"]
        return selected
    monkeypatch.setattr(execution_snapshot, "capture_execution_snapshot", capture)
    row = custom_agent_service._record_execution_save(
        db, head, expected_revision_id=old.id, output_contract=selected.output_contract,
        previous_snapshot=saved, previous_output=saved.output_contract, active_group_ids=["FB"],
    )
    db.refresh(head)
    assert head.execution_revision_id == row.id
    assert head.inherited_allowed_group_ids == ["FB"]
    with pytest.raises(ValueError, match="cannot widen"):
        custom_agent_service._validate_inherited_access_floor(head, [])


@pytest.mark.parametrize("change", [
    "missing", "null", "empty", "wrong_type", "missing_package", "numeric_agent",
    "blank_domain", "unknown_key", "schema_and_builder", "none_and_builder", "wrong_curation",
])
def test_sql_cannot_bypass_builder_contract(builder_db, change):
    db, head, _, saved, _ = builder_db
    snapshot = builder_snapshot(saved).model_dump(mode="json")
    contract = snapshot["output_contract"]
    ref = contract["domain_extraction_ref"]
    if change == "missing":
        del contract["domain_extraction_ref"]
    elif change == "null":
        contract["domain_extraction_ref"] = None
    elif change == "empty":
        contract["domain_extraction_ref"] = {}
    elif change == "wrong_type":
        contract["domain_extraction_ref"] = []
    elif change == "missing_package":
        del ref["package_id"]
    elif change == "numeric_agent":
        ref["agent_id"] = 123
    elif change == "blank_domain":
        ref["domain_pack_id"] = " "
    elif change == "unknown_key":
        ref["extra"] = "value"
    elif change == "schema_and_builder":
        contract["output_schema_key"] = "GeneExtractionResultEnvelope"
    elif change == "none_and_builder":
        contract.update(output_state="none", output_mode=None)
    elif change == "wrong_curation":
        snapshot["curation"]["domain_pack_id"] = "agr.alliance.allele"
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(sa.insert(AgentExecutionRevision).values(
                id=uuid4(), agent_id=head.id, revision=2, creator_id=1,
                fingerprint="sha256:" + "a" * 64, snapshot=snapshot,
                output_state=contract["output_state"], output_mode=contract["output_mode"],
                output_schema_key=contract["output_schema_key"],
            ))
