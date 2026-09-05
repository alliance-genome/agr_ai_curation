"""Authorized Workshop validator revisions reusing a packaged input contract.

A custom prompt is not a schema declaration. Only saved validators derived from
an opted-in packaged validator, retaining its result schema, can reuse its slots.
The capability identity includes the immutable executable revision, never a head.
"""
from dataclasses import replace
from uuid import UUID

from sqlalchemy import select

CUSTOM_BINDING_SEPARATOR = "--custom--"


def custom_validator_capabilities(packaged, *, user_id, active_group_ids, references=()):
    from src.lib.agent_studio.custom_agent_service import list_custom_agents_visible_to_user
    from src.lib.agent_studio.execution_revision_service import get_execution_revision, ExecutionRevisionNotFoundError
    from src.lib.config.agent_loader import canonical_system_agent_key, get_agent_definition_for_package
    from src.models.sql.agent import Agent
    from src.models.sql.agent_execution_revision import AgentExecutionRevision
    from src.models.sql.database import SessionLocal
    from src.schemas.domain_validator import is_domain_validator_result_schema
    from src.lib.config.schema_discovery import resolve_output_schema

    if user_id is None:
        return []
    by_binding = {cap.key(): cap for cap in packaged}
    result = []
    with SessionLocal() as db:
        candidates = {(agent.id, agent.execution_revision_id) for agent in list_custom_agents_visible_to_user(db, user_id)
                      if agent.execution_revision_id is not None}
        # Previously saved pins stay exact even after the custom head advances.
        for ref in references:
            if CUSTOM_BINDING_SEPARATOR not in ref.binding_id:
                continue
            try:
                revision_id = UUID(ref.binding_id.rsplit(CUSTOM_BINDING_SEPARATOR, 1)[1])
            except ValueError:
                continue
            revision = db.get(AgentExecutionRevision, revision_id)
            if revision is not None:
                candidates.add((revision.agent_id, revision.id))
        for agent_id, revision_id in candidates:
            try:
                revision, saved = get_execution_revision(db, agent_id, revision_id, user_id,
                                                        active_group_ids=list(active_group_ids))
            except (ExecutionRevisionNotFoundError, ValueError):
                continue
            schema_key = saved.output_contract.output_schema_key
            if saved.output_contract.output_mode != "domain" or not schema_key:
                continue
            if not is_domain_validator_result_schema(resolve_output_schema(schema_key)):
                continue
            agent = db.get(Agent, agent_id)
            for capability in by_binding.values():
                source = capability.binding
                ref = source.validator_agent
                definition = get_agent_definition_for_package(ref.package_id, ref.agent_id) if ref else None
                if definition is None or saved.template_source != canonical_system_agent_key(definition):
                    continue
                if schema_key != definition.output_schema:
                    continue
                binding_id = capability.ref.binding_id + CUSTOM_BINDING_SEPARATOR + str(revision.id)
                pin = {"agent_id": str(agent_id), "agent_key": agent.agent_key,
                       "revision_id": str(revision.id), "fingerprint": revision.fingerprint}
                binding = replace(source, binding_id=binding_id, display_name=agent.name,
                                  batch_enabled=False, raw={**source.raw, "custom_validator": pin})
                result.append(replace(capability, ref=capability.ref.model_copy(update={"binding_id": binding_id}),
                                      binding=binding))
    return result


def runtime_validator_user_id(identity=None):
    """Resolve the request's authenticated identity; never use the profile owner."""
    from src.lib.context import get_current_user_id
    from src.models.sql.database import SessionLocal
    from src.models.sql.user import User
    if identity is None:
        identity = get_current_user_id()
    if identity is None:
        return None
    if str(identity).isdigit():
        return int(identity)
    with SessionLocal() as db:
        return db.execute(select(User.id).where(User.auth_sub == identity)).scalar_one_or_none()


def build_custom_validator_agent(pin, runtime_context):
    """Reauthorize the exact custom revision again at dispatch, including tools."""
    from src.lib.agent_studio.catalog_service import get_agent_by_id
    identity = runtime_context.user_id if runtime_context else None
    user_id = runtime_validator_user_id(identity)
    if user_id is None:
        raise ValueError("Authenticated user is required for custom validator execution")
    agent = get_agent_by_id(
        pin["agent_key"], execution_revision_id=pin["revision_id"], db_user_id=user_id,
        user_id=identity if identity is not None else str(user_id),
        document_id=runtime_context.document_id if runtime_context else None,
        authenticated_groups=list(runtime_context.authenticated_groups or ()) if runtime_context else [],
    )
    if agent.execution_receipt["fingerprint"] != pin["fingerprint"]:
        raise ValueError("Custom validator revision fingerprint changed")
    return agent
