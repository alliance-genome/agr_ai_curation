"""Protected executable revision reads and transaction-local appends."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.agent_access import is_resource_access_allowed, require_allowed_group_ids_narrowing
from src.lib.agent_studio.agent_service import get_project_ids_for_user
from src.lib.agent_studio.generic_profile_service import get_profile_revision, ProfileNotFoundError
from src.models.sql.agent import Agent
from src.models.sql.agent_execution_revision import AgentExecutionRevision
from src.schemas.agent_execution_revision import AgentExecutionSnapshot, AgentExecutionReceipt


class ExecutionRevisionNotFoundError(ValueError):
    pass


class ExecutionRevisionConflictError(ValueError):
    pass


def current_execution_receipt(
    db: Session, agent_key: str, user_id: int, *, active_group_ids: list[str],
) -> AgentExecutionReceipt:
    """Pin an authorized head before durable turn creation; never build a model."""
    head = db.execute(select(Agent).where(Agent.agent_key == agent_key)).scalar_one_or_none()
    if head is None or not head.is_active or head.execution_revision_id is None:
        raise ExecutionRevisionNotFoundError("Executable agent revision not found")
    revision, saved = get_execution_revision(
        db, head.id, head.execution_revision_id, user_id, active_group_ids=active_group_ids,
    )
    return AgentExecutionReceipt(
        agent_id=head.id, agent_key=head.agent_key, agent_revision_id=revision.id,
        revision=revision.revision, fingerprint=revision.fingerprint,
        output_contract=saved.output_contract,
    )


def authorize_execution_receipt(
    db: Session, receipt: dict | None, user_id: int, *, active_group_ids: list[str],
) -> AgentExecutionReceipt:
    """Reauthorize a durable pin without consulting today's executable head."""
    expected = AgentExecutionReceipt.model_validate(receipt)
    head = _get_visible_agent(db, expected.agent_id, user_id)
    revision, saved = get_execution_revision(
        db, head.id, expected.agent_revision_id, user_id, active_group_ids=active_group_ids,
    )
    actual = AgentExecutionReceipt(
        agent_id=head.id, agent_key=head.agent_key, agent_revision_id=revision.id,
        revision=revision.revision, fingerprint=revision.fingerprint,
        output_contract=saved.output_contract,
    )
    if actual != expected:
        raise ValueError("Executable agent receipt does not match the authorized revision")
    return actual


def baseline_current_execution_heads(db: Session) -> int:
    """Migration-only capture of current custom heads, never partial history.

    The caller owns the transaction. An unresolved template or invalid current
    configuration aborts the migration instead of creating a fictional revision.
    Existing immutable heads are left untouched on an explicit retry.
    """
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.prompts import cache
    from src.lib.agent_studio.domain_output_contract import initial_agent_output_contract

    agents = db.execute(
        select(Agent)
        .where(Agent.agent_key.startswith("ca_", autoescape=True))
        .where(Agent.execution_revision_id.is_(None))
        .order_by(Agent.id)
        .with_for_update()
    ).scalars().all()
    # Migration sessions run before application startup. Resolve the database's
    # actual prompt layers, not an incidental cache from another database.
    if any(agent.template_source or agent.group_rules_component for agent in agents):
        cache.initialize(db)
    for agent in agents:
        if agent.user_id is None:
            raise ValueError("Cannot baseline a custom agent without its owner")
        saved = capture_execution_snapshot(
            db, agent, initial_agent_output_contract(agent)
        )
        if saved.output_contract.domain_extraction_ref is not None:
            agent.inherited_allowed_group_ids = list(saved.inherited_allowed_group_ids)
        append_execution_revision(
            db, agent, saved, user_id=agent.user_id, expected_revision_id=None
        )
    return len(agents)


def _validated_snapshot(
    db: Session, row: AgentExecutionRevision, user_id: int
) -> AgentExecutionSnapshot:
    saved = AgentExecutionSnapshot.model_validate(row.snapshot)
    if saved.fingerprint() != row.fingerprint:
        raise ValueError("Executable revision fingerprint mismatch")
    pin = saved.output_contract.generic_profile_ref
    if pin is not None:
        try:
            profile = get_profile_revision(
                db, pin.profile_id, pin.revision, user_id, include_archived=True
            )
        except ProfileNotFoundError as exc:
            raise ExecutionRevisionNotFoundError("Executable agent revision not found") from exc
        if (
            profile.id != pin.profile_revision_id
            or profile.fingerprint != pin.fingerprint
        ):
            raise ValueError("Executable revision profile identity mismatch")
    return saved


def get_execution_revision(
    db: Session,
    agent_id: UUID,
    revision_id: UUID,
    user_id: int,
    *,
    active_group_ids: list[str],
) -> tuple[AgentExecutionRevision, AgentExecutionSnapshot]:
    """Current visibility + saved access bounds; archived pins remain usable."""
    _get_visible_agent(db, agent_id, user_id)
    row = db.execute(
        select(AgentExecutionRevision).where(
            AgentExecutionRevision.id == revision_id,
            AgentExecutionRevision.agent_id == agent_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExecutionRevisionNotFoundError("Executable agent revision not found")
    saved = _validated_snapshot(db, row, user_id)
    if not is_resource_access_allowed(
        visibility_allowed=True,
        allowed_group_ids=saved.allowed_group_ids,
        active_group_ids=active_group_ids,
        resource_kind="custom_agent_revision",
    ):
        raise ExecutionRevisionNotFoundError("Executable agent revision not found")
    return row, saved


def _get_visible_agent(db: Session, agent_id: UUID, user_id: int) -> Agent:
    agent = db.execute(select(Agent).where(Agent.id == agent_id)).scalar_one_or_none()
    if agent is None or not agent.agent_key.startswith("ca_"):
        raise ExecutionRevisionNotFoundError("Executable agent revision not found")
    visible = (agent.visibility == "private" and agent.user_id == user_id) or (
        agent.visibility == "project"
        and agent.project_id in get_project_ids_for_user(db, user_id)
    )
    if not visible:
        raise ExecutionRevisionNotFoundError("Executable agent revision not found")
    return agent


def list_execution_revisions(
    db: Session, agent_id: UUID, user_id: int, *, active_group_ids: list[str],
    before_revision: int | None = None,
) -> tuple[list[tuple[AgentExecutionRevision, AgentExecutionSnapshot]], int | None]:
    from src.lib.openai_agents.config import get_generic_profile_list_page_size

    _get_visible_agent(db, agent_id, user_id)
    query = select(AgentExecutionRevision).where(AgentExecutionRevision.agent_id == agent_id)
    if before_revision is not None:
        query = query.where(AgentExecutionRevision.revision < before_revision)
    page_size = get_generic_profile_list_page_size()
    rows = list(db.execute(query.order_by(AgentExecutionRevision.revision.desc()).limit(page_size + 1)).scalars())
    next_revision = rows[page_size - 1].revision if len(rows) > page_size else None
    results = []
    for row in rows[:page_size]:
        # Historical access restrictions can differ from the current head.
        # Skip inaccessible snapshots without disclosing their prompt/settings.
        saved = AgentExecutionSnapshot.model_validate(row.snapshot)
        if not is_resource_access_allowed(
            visibility_allowed=True, allowed_group_ids=saved.allowed_group_ids,
            active_group_ids=active_group_ids, resource_kind="custom_agent_revision",
        ):
            continue
        results.append((row, _validated_snapshot(db, row, user_id)))
    return results, next_revision


def append_execution_revision(
    db: Session,
    agent: Agent,
    snapshot: AgentExecutionSnapshot,
    *,
    user_id: int,
    expected_revision_id: UUID | None,
    allow_archived_profile: bool = False,
    notes: str | None = None,
) -> AgentExecutionRevision:
    """Insert immutable bytes and advance one head; never commits separately."""
    saved = AgentExecutionSnapshot.model_validate(snapshot.model_dump(mode="json"))
    # Lock before allocation. populate_existing refreshes a stale ORM identity
    # without overwriting flushed local edits made in this same transaction.
    db.flush()
    locked = db.execute(
        select(Agent)
        .where(Agent.id == agent.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.user_id != user_id or not locked.agent_key.startswith("ca_"):
        raise ExecutionRevisionNotFoundError("Executable agent revision not found")
    if locked.execution_revision_id != expected_revision_id:
        raise ExecutionRevisionConflictError(
            "Agent changed since it was opened; compare before saving"
        )
    revision_number = 1
    if expected_revision_id is not None:
        previous = db.execute(
            select(AgentExecutionRevision).where(
                AgentExecutionRevision.agent_id == agent.id,
                AgentExecutionRevision.id == expected_revision_id,
            )
        ).scalar_one()
        revision_number = previous.revision + 1
    pin = saved.output_contract.generic_profile_ref
    if pin is not None:
        profile = get_profile_revision(
            db, pin.profile_id, pin.revision, user_id,
            include_archived=allow_archived_profile,
        )
        if (
            profile.id != pin.profile_revision_id
            or profile.fingerprint != pin.fingerprint
        ):
            raise ValueError("Selected profile revision identity mismatch")
    row = AgentExecutionRevision(
        agent_id=agent.id,
        revision=revision_number,
        creator_id=user_id,
        fingerprint=saved.fingerprint(),
        snapshot=saved.model_dump(mode="json"),
        notes=notes,
        output_state=saved.output_contract.output_state,
        output_mode=saved.output_contract.output_mode,
        output_schema_key=saved.output_contract.output_schema_key,
        profile_revision_id=pin.profile_revision_id if pin else None,
        profile_fingerprint=pin.fingerprint if pin else None,
    )
    db.add(row)
    db.flush()
    locked.execution_revision_id = row.id
    # Keep the editable head's packaged schema aligned with the explicit state.
    locked.output_schema_key = saved.output_contract.output_schema_key
    db.flush()
    return row


def restore_execution_revision(
    db: Session,
    agent_id: UUID,
    revision_id: UUID,
    *,
    user_id: int,
    expected_revision_id: UUID,
    active_group_ids: list[str],
) -> AgentExecutionRevision:
    """Append a complete saved configuration as the new head, never re-resolve it.

    The immutable target and prior head remain intact. Current visibility,
    ownership and inherited access bounds still apply; presentation metadata is
    not part of the execution snapshot and is not reverted.
    """
    from copy import deepcopy

    head = db.execute(
        select(Agent).where(Agent.id == agent_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if head is None or not head.is_active or head.user_id != user_id or not head.agent_key.startswith("ca_"):
        raise ExecutionRevisionNotFoundError("Executable agent revision not found")
    if head.execution_revision_id != expected_revision_id:
        raise ExecutionRevisionConflictError("Agent changed since it was opened; compare before reverting")
    _, saved = get_execution_revision(
        db, agent_id, revision_id, user_id, active_group_ids=active_group_ids
    )
    require_allowed_group_ids_narrowing(
        list(head.inherited_allowed_group_ids or []), saved.inherited_allowed_group_ids,
        source_name="current inherited access floor",
    )
    # No current template, prompt normalizer or recapture is involved. In
    # particular, saved resolved main/group prompt bytes remain unchanged.
    for field in (
        "model_id", "model_temperature", "model_reasoning", "instructions",
        "tool_ids", "group_tool_policy", "allowed_group_ids",
        "inherited_allowed_group_ids", "group_rules_enabled",
        "group_rules_component", "group_prompt_overrides", "template_source",
    ):
        setattr(head, field, deepcopy(getattr(saved, field)))
    head.version = int(head.version or 1) + 1
    return append_execution_revision(
        db, head, saved, user_id=user_id, expected_revision_id=expected_revision_id,
        allow_archived_profile=True,
    )
