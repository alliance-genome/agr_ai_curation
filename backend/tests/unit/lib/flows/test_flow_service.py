from unittest.mock import Mock

from sqlalchemy import inspect

from src.api import flows as flows_api
from src.lib.flows import flow_service
from src.models.sql.curation_flow import CurationFlow
from tests.unit.lib.flows.test_execution_revisions import flow


def test_save_validates_like_a_curator_save_and_marks_the_column_changed(monkeypatch):
    validated = {"nodes": [], "edges": [], "entry_node_id": "task"}
    validate = Mock(return_value=validated)
    monkeypatch.setattr(flows_api, "_validated_flow_definition_payload", validate)
    row = CurationFlow(user_id=7, name="Flow", flow_definition={"old": True})
    db = Mock()
    definition = flow(None)
    flow_service.save_flow_definition(db, row, definition, active_group_ids=["group-a"])
    validate.assert_called_once_with(
        definition, db_user_id=7, enforce_agent_references=True, enforce_agent_step_policy=True,
        active_group_ids=["group-a"], db=db)
    assert row.flow_definition == validated
    assert inspect(row).attrs.flow_definition.history.has_changes()
    db.commit.assert_not_called()
