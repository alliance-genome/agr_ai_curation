"""Saving a flow definition, shared by the flows API and maintenance scripts."""

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.sql.curation_flow import CurationFlow
from src.schemas.flows import FlowDefinition


def save_flow_definition(
    db: Session, flow: CurationFlow, definition: FlowDefinition, *, active_group_ids: list[str],
) -> None:
    """Validate ``definition`` exactly as a curator save does and store it; the caller commits.

    Validation runs as the flow's owner with agent references and step policy
    enforced, including pinned-revision and profile projection checks. An invalid
    flow raises the validator's HTTPException (422) with its findings.
    """
    # The validator and its agent-policy helpers live with the flows route.
    from src.api import flows as flows_api

    flow.flow_definition = flows_api._validated_flow_definition_payload(
        definition,
        db_user_id=flow.user_id,
        enforce_agent_references=True,
        enforce_agent_step_policy=True,
        active_group_ids=active_group_ids,
        db=db,
    )
    # SQLAlchemy does not detect in-place JSONB changes; flag the column explicitly.
    flag_modified(flow, "flow_definition")
