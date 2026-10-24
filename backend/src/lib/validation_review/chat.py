"""Authenticated chat review phases, separate from ordinary extraction/formatting.

A chat operation discovers artifacts; its shared quota never depends on how many
extractors the supervisor happens to call. Saved plans and CHAT source rows make
pending work discoverable without replaying extraction or replacing source data.
"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict

from sqlalchemy import select

from src.lib.validation_history import store, runtime
from src.schemas.domain_envelope import DomainEnvelope
from . import admission, reviewer
from .decisions import ReviewContext
from .validator_adapter import ValidatorAdapter, save_plan

ACTIVE_CHAT_REVIEW: ContextVar[ChatReview | None] = ContextVar(
    "chat_validation_review", default=None
)


class DirectChatAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["continue_review", "new_extraction"]
    explanation: str


@dataclass
class ChatReview:
    owner_id: str
    document_id: str
    session_id: str
    turn_id: str
    supervisor: Any
    authenticated_groups: tuple[str, ...]
    user_request: str
    phase_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reviewed: set[str] = field(default_factory=set)

    async def until_stop(self, awaitable):
        """Stop interrupts non-streaming phases; submitted late answers stay durable."""
        from src.lib.redis_client import check_cancel_signal

        async def watch():
            while not await check_cancel_signal(self.session_id):
                await asyncio.sleep(
                    0.2
                )  # Internal cancellation polling, not an operational limit.

        task = asyncio.ensure_future(awaitable)
        watcher = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait(
                (task, watcher), return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done:
                watcher.result()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                from .stops import ReviewInterrupted

                raise ReviewInterrupted("Curator stopped validation review")
            return await task
        finally:
            watcher.cancel()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, watcher, return_exceptions=True)

    async def direct_action(self, extractor, input_items):
        """The supervisor interprets intent; code can invoke only the bound extractor.

        This is ordinary turn routing, not a new review operation or a reset of
        saved review allowances. An uncertain routing call is not replayed.
        """
        from src.lib.observability.cost_context import (
            get_agent_cost_identity,
            attach_agent_cost_identity,
        )
        from src.lib.openai_agents.runner import run_agent_with_owned_openai_resources
        from src.lib.openai_agents.config import get_max_turns
        from . import chat_dispatch

        if not await asyncio.to_thread(self.records):
            return DirectChatAction(
                action="new_extraction",
                explanation="Initial request to the selected agent.",
            )
        extractor_id = get_agent_cost_identity(extractor)["agent_id"]
        fence = await asyncio.to_thread(
            chat_dispatch.claim,
            self,
            "direct-intent:" + extractor_id,
            self.user_request,
        )
        if not fence["claimed"]:
            if fence["state"] == "completed":
                return DirectChatAction.model_validate(fence["result"])
            return DirectChatAction(
                action="continue_review",
                explanation="The previous routing request did not finish. No new extraction was started.",
            )
        policy = "\n\n".join(
            layer.content
            for layer in getattr(self.supervisor, "validation_review_policy_layers", ())
        )
        instructions = (
            policy
            + "\n\n"
            + (
                "Interpret the curator's current request for this preferred-agent chat. "
                "Saved extraction and validation history already exist. Choose continue_review "
                "for continuing, reconsidering, discussing or checking those saved results. "
                "Choose new_extraction ONLY when the curator explicitly requests fresh extraction. "
                "When ambiguous, continue_review and explain what needs clarification. "
                "You cannot invoke any tools or judge scientific validation in this routing step. "
                "new_extraction authorizes only the chosen extractor identified below; never another agent. "
                "Do not claim extraction or review has already been completed."
            )
        )
        router = self.supervisor.clone(
            name=self.supervisor.name + " Intent",
            instructions=instructions,
            tools=[],
            handoffs=[],
            output_type=DirectChatAction,
        )
        router.tool_surface_base_instructions = instructions
        attach_agent_cost_identity(router, get_agent_cost_identity(self.supervisor))
        from src.lib.prompts.assembly import _bundle, _make_layer
        from src.lib.prompts.context import (
            set_pending_prompts,
            bind_prompt_run,
            commit_pending_prompts,
        )
        from src.lib.openai_agents.langfuse_client import log_agent_config

        bundle = _bundle(
            "supervisor",
            [
                _make_layer(
                    layer_id="supervisor:direct_intent",
                    kind="runtime_context",
                    title="Direct chat intent",
                    content=instructions,
                    provenance="backend:validation_review",
                    editable=False,
                    locked=True,
                    source_ref="src.lib.validation_review.chat:ChatReview.direct_action",
                )
            ],
        )
        prompt_id = set_pending_prompts(
            router.name,
            [],
            effective_prompt_hash=bundle.hash,
            layer_manifest=bundle.to_manifest(),
        )
        bind_prompt_run(router, prompt_id)
        commit_pending_prompts(router)
        log_agent_config(
            agent_name=router.name,
            instructions=instructions,
            model=str(router.model),
            tools=[],
            metadata={
                "chosen_extractor": extractor_id,
                "layer_manifest": bundle.to_manifest(),
            },
        )
        reviewer.flush_phase_config()
        result = await self.until_stop(
            run_agent_with_owned_openai_resources(
                router,
                json.dumps(
                    {
                        "chosen_extractor": extractor_id,
                        "conversation": input_items,
                        "current_request": self.user_request,
                        "saved_review": await asyncio.to_thread(self.status),
                    },
                    default=str,
                ),
                max_turns=get_max_turns(),
            )
        )
        action = DirectChatAction.model_validate(result.final_output)
        await asyncio.to_thread(
            chat_dispatch.complete, self, fence["id"], action.model_dump()
        )
        return action

    def operation_scope(self, scope):
        return admission.create_scope(
            scope,
            operation_id="chat:"
            + store.canonical(
                {
                    "session_id": self.session_id,
                    "turn_id": self.turn_id,
                    "document_id": self.document_id,
                }
            )[0],
            kind="discovered_document_run",
            targets=0,
            episodes=0,
        )

    async def receive(
        self,
        envelope,
        domain_pack,
        *,
        agent_key,
        adapter_key,
        tool_name,
        execution_receipt,
        context,
        trace_id=None,
        source_metadata=None,
        builder_finalization=None,
        event_emitter=None,
    ):
        from src.lib.domain_packs.validator_dispatch import prepare_validator_dispatch
        from src.lib.curation_workspace.extraction_results import (
            persist_inline_validated_extraction_result,
        )
        from src.schemas.curation_workspace import CurationExtractionSourceKind
        from src.models.sql.validation_history import ValidationFact

        binding = runtime.bind_artifact(envelope, context)

        def existing_plan():
            with store.SessionLocal() as db:
                store._artifact(db, binding.artifact_id, binding.scope)
                fact = db.scalar(
                    select(ValidationFact).where(
                        ValidationFact.artifact_id == binding.artifact_id,
                        ValidationFact.kind == "review_chat_plan",
                    )
                )
                return (fact.id, store._content(db, fact)) if fact else None

        existing = await asyncio.to_thread(existing_plan)
        if existing is None:
            plan = await asyncio.to_thread(
                prepare_validator_dispatch,
                envelope,
                domain_pack,
                runtime_context=context,
            )
            plan.envelope.metadata["validation_review_runtime"] = "chat"
            plan_ref = await asyncio.to_thread(save_plan, plan, context=context)
            envelope = plan.envelope
        else:
            plan_ref, body = existing
            envelope = DomainEnvelope.model_validate(body["envelope"])
        episode_id = envelope.metadata["review_episode_id"]
        # Source persistence precedes provider work and is never rewritten with a
        # correction. Selected projections live in the checkpoint/history ledger.
        await asyncio.to_thread(
            persist_inline_validated_extraction_result,
            payload_json=envelope.model_dump(mode="json"),
            execution_receipt=execution_receipt,
            document_id=self.document_id,
            agent_key=agent_key,
            adapter_key=adapter_key,
            tool_name=tool_name,
            source_kind=CurationExtractionSourceKind.CHAT,
            origin_session_id=self.session_id,
            user_id=self.owner_id,
            trace_id=trace_id,
            builder_finalization=builder_finalization,
            metadata={
                **(source_metadata or {}),
                "chat_turn_id": self.turn_id,
                "chat_review": {
                    "plan_ref": plan_ref,
                    "artifact_id": binding.artifact_id,
                    "episode_id": episode_id,
                    "history_scope_id": binding.scope.session_id,
                    "operation_turn_id": self.turn_id,
                },
            },
        )
        scope_id = await asyncio.to_thread(self.operation_scope, binding.scope)
        await asyncio.to_thread(
            admission.attach_episode,
            binding.scope,
            artifact_id=binding.artifact_id,
            episode_id=episode_id,
            scope_id=scope_id,
        )
        adapter = ValidatorAdapter(
            binding.scope,
            binding.artifact_id,
            episode_id,
            plan_ref,
            authenticated_groups=self.authenticated_groups,
            event_emitter=event_emitter,
        )
        try:
            await self.until_stop(adapter.initial())
        except Exception as error:
            from .stops import review_stop

            if review_stop(error) is None:
                raise
        return envelope.model_dump_json()

    def records(self):
        from src.lib.curation_workspace.models import CurationExtractionResultRecord
        from src.lib.curation_workspace.extraction_results import _record_to_schema

        with store.SessionLocal() as db:
            store._authorize(
                db, store.HistoryScope(self.owner_id, self.document_id, self.session_id)
            )
            rows = db.scalars(
                select(CurationExtractionResultRecord)
                .where(
                    CurationExtractionResultRecord.user_id == self.owner_id,
                    CurationExtractionResultRecord.document_id
                    == UUID(self.document_id),
                    CurationExtractionResultRecord.origin_session_id == self.session_id,
                    CurationExtractionResultRecord.extraction_metadata.has_key(
                        "chat_review"
                    ),
                )
                .order_by(CurationExtractionResultRecord.created_at)
            )
            return [_record_to_schema(row) for row in rows]

    def context(self, record):
        from src.lib.observability.cost_context import get_agent_cost_identity

        ref = record.metadata["chat_review"]
        scope = store.HistoryScope(
            self.owner_id, self.document_id, ref["history_scope_id"]
        )
        return ReviewContext(
            scope,
            ref["episode_id"],
            get_agent_cost_identity(self.supervisor)["agent_id"] + ".validation_review",
        )

    async def review_result(self, record):
        context = self.context(record)
        if context.episode_id in self.reviewed:
            return None
        self.reviewed.add(context.episode_id)
        saved = await asyncio.to_thread(self.saved_review_outcome, record)
        if saved is not None:
            return saved
        ref = record.metadata["chat_review"]
        adapter = ValidatorAdapter(
            context.history_scope,
            ref["artifact_id"],
            ref["episode_id"],
            ref["plan_ref"],
            authenticated_groups=self.authenticated_groups,
        )

        def checkpoint():
            return self.checkpoint(record)

        try:
            await adapter.initial()
            await adapter.advance(checkpoint)
            agent = reviewer.create_supervisor_phase(
                context,
                supervisor=self.supervisor,
                checkpoint_provider=checkpoint,
                authenticated_groups=self.authenticated_groups,
                execute_work=adapter.execute,
                advance_work=lambda: adapter.advance(checkpoint),
                prepare_inputs=lambda: adapter.prepare_inputs(checkpoint),
                refresh_inputs=adapter.refresh_inputs,
            )
            from src.lib.openai_agents.extraction_trace_events import (
                write_extraction_trace_event,
            )

            write_extraction_trace_event(
                event_type="validation_review.chat_phase",
                output_summary={
                    "actor_id": context.actor_id,
                    "episode_id": context.episode_id,
                    "artifact_id": ref["artifact_id"],
                    "plan_ref": ref["plan_ref"],
                    "phase": "supervisor_review",
                    "tools": [tool.name for tool in agent.tools],
                },
            )
            return await reviewer.run(
                context,
                agent,
                input=json.dumps(
                    {
                        "user_request": self.user_request,
                        "instruction": "Review this saved extraction. Read the review packet first. Record explicit decisions; do not re-extract.",
                        "result_ref": (
                            "extraction-result:" + record.extraction_result_id
                        ),
                    }
                ),
            )
        except Exception as error:
            from .stops import review_stop

            stopped = review_stop(error)
            if stopped is not None:
                return await reviewer._stopped_response(context, stopped)
            raise

    async def resume(self):
        async with self.phase_lock:
            return await self.until_stop(self._resume())

    async def _resume(self):
        records = await asyncio.to_thread(self.records)
        outcomes = []
        for record in records:
            context = self.context(record)
            # Preserve the original operation policy, deadline and charges.
            ref = record.metadata["chat_review"]
            scope_id = await asyncio.to_thread(
                admission.create_scope,
                context.history_scope,
                operation_id="chat:"
                + store.canonical(
                    {
                        "session_id": self.session_id,
                        "turn_id": ref["operation_turn_id"],
                        "document_id": self.document_id,
                    }
                )[0],
                kind="discovered_document_run",
                targets=0,
                episodes=0,
            )
            await asyncio.to_thread(
                admission.attach_episode,
                context.history_scope,
                artifact_id=ref["artifact_id"],
                episode_id=ref["episode_id"],
                scope_id=scope_id,
            )
            status = await asyncio.to_thread(
                admission.status, context.history_scope, context.episode_id
            )
            if not status["ready"]:
                outcome = await self.review_result(record)
                if outcome is not None:
                    await asyncio.to_thread(self.retain_review_outcome, record, outcome)
                    outcomes.append(outcome)
        return outcomes

    def review_summary_operation(self, context):
        return "chat-review-summary:" + store.canonical(
            {
                "turn_id": self.turn_id,
                "episode_id": context.episode_id,
                "actor_id": context.actor_id,
            }
        )[0]

    def saved_review_outcome(self, record):
        """A reconnect reuses the completed phase for this exact durable turn."""
        from src.models.sql.validation_history import ValidationFact

        context = self.context(record)
        ref = record.metadata["chat_review"]
        with store.SessionLocal() as db:
            store._artifact(db, ref["artifact_id"], context.history_scope)
            fact = db.scalar(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == ref["artifact_id"],
                    ValidationFact.operation_id == self.review_summary_operation(context),
                    ValidationFact.kind == "review_supervisor_summary",
                )
            )
            return store._content(db, fact)["outcome"] if fact is not None else None

    def retain_review_outcome(self, record, outcome):
        """Keep model explanations and expected stop reasons as immutable history."""
        from .audit import append_fact

        context = self.context(record)
        ref = record.metadata["chat_review"]
        with store.SessionLocal.begin() as db:
            store._artifact(db, ref["artifact_id"], context.history_scope)
            append_fact(
                db,
                ref["artifact_id"],
                operation_id=self.review_summary_operation(context),
                kind="review_supervisor_summary",
                parent_id=context.episode_id,
                body={
                    "result_ref": "extraction-result:" + record.extraction_result_id,
                    "episode_id": context.episode_id,
                    "actor_id": context.actor_id,
                    "turn_id": self.turn_id,
                    "outcome": outcome,
                },
            )

    def review_notes(self, *, result_refs=None):
        """Latest phase explanation per result; older explanations remain auditable."""
        from src.models.sql.validation_history import ValidationFact

        notes = []
        for record in self.records():
            result_ref = "extraction-result:" + record.extraction_result_id
            if result_refs is not None and result_ref not in result_refs:
                continue
            ref = record.metadata["chat_review"]
            scope = store.HistoryScope(
                self.owner_id, self.document_id, ref["history_scope_id"]
            )
            with store.SessionLocal() as db:
                store._artifact(db, ref["artifact_id"], scope)
                fact = db.scalar(
                    select(ValidationFact)
                    .where(
                        ValidationFact.artifact_id == ref["artifact_id"],
                        ValidationFact.kind == "review_supervisor_summary",
                        ValidationFact.parent_id == ref["episode_id"],
                    )
                    .order_by(ValidationFact.sequence.desc())
                    .limit(1)
                )
                if fact is not None:
                    notes.append(store._content(db, fact))
        return notes

    def supervisor_handoff(self):
        return {"status": self.status(), "review_outcomes": self.review_notes()}

    def render_review_notes(self, *, result_refs=None):
        notes = []
        for entry in self.review_notes(result_refs=result_refs):
            outcome = entry["outcome"]
            output = outcome.get("output")
            if output:
                notes.append(str(output))
            reason = outcome.get("reason")
            if reason and reason != "complete":
                notes.append(
                    "Review paused: "
                    + reason.replace("_", " ")
                    + ". Saved results and history are retained."
                )
        return "\n\nReviewer's notes:\n" + "\n\n".join(notes) if notes else ""

    def status(self):
        items = []
        for record in self.records():
            ref = record.metadata["chat_review"]
            scope = store.HistoryScope(
                self.owner_id, self.document_id, ref["history_scope_id"]
            )
            status = admission.status(scope, ref["episode_id"])
            items.append(
                {
                    "result_ref": ("extraction-result:" + record.extraction_result_id),
                    "ready": status["ready"],
                    "state": status["state"],
                    "episode_id": ref["episode_id"],
                }
            )
        return {
            "document_id": self.document_id,
            "results": items,
            "pending_count": sum(not item["ready"] for item in items),
        }

    def direct_delivery(self, *, current_turn_only=False):
        records = self.records()
        refs = {
            "extraction-result:" + record.extraction_result_id
            for record in records
            if not current_turn_only
            or record.metadata["chat_review"]["operation_turn_id"] == self.turn_id
        }
        status = self.status()
        items = [item for item in status["results"] if item["result_ref"] in refs]
        status = {
            **status,
            "results": items,
            "pending_count": sum(not item["ready"] for item in items),
            "presentation": "selected_values",
        }
        return self.selected_presentation(result_refs=refs), status

    def selected_presentation(self, *, result_refs=None):
        """Render current selections, never relabel immutable extraction as reviewed."""
        from src.lib.validation_history import projection

        results = []
        for record in self.records():
            if (
                result_refs is not None
                and "extraction-result:" + record.extraction_result_id
                not in result_refs
            ):
                continue
            ref = record.metadata["chat_review"]
            scope = store.HistoryScope(
                self.owner_id, self.document_id, ref["history_scope_id"]
            )
            publication = projection.latest_publication_reference(
                scope, ref["artifact_id"]
            )
            if publication is None:
                continue
            try:
                selected, _ = projection.read_published_projection(
                    scope, publication, current=True
                )
            except store.SelectionNotReady:
                continue
            results.append(
                {
                    "objects": [
                        {"type": obj.object_type, "values": obj.payload}
                        for obj in selected.extracted_objects
                    ]
                }
            )
        notes = self.render_review_notes(result_refs=result_refs)
        if not results:
            return (
                "No current reviewed selection is available yet. Any saved original extraction remains accessible in the saved results."
                + notes
            )
        return (
            "Current saved values, including accepted corrections (review readiness is shown separately):\n\n```json\n"
            + json.dumps(results, indent=2)
            + "\n```"
            + notes
        )

    def checkpoint(self, record):
        from src.schemas.execution_provenance import ExtractionExecutionContext
        from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
        from src.lib.curation_workspace.curation_prep_service import (
            _checkpoint_project_key,
        )
        from src.lib.domain_envelopes.persistence import domain_envelope_payload_hash
        from src.lib.curation_workspace.models import DomainEnvelopeModel as EnvelopeRow
        from src.lib.curation_workspace.domain_envelope_normalization import (
            domain_envelope_from_extraction_result,
        )

        original = domain_envelope_from_extraction_result(record, stored=True)
        with store.SessionLocal() as db:
            store._artifact(
                db,
                original.metadata["validation_history"]["artifact_id"],
                self.context(record).history_scope,
            )
            row = db.get(EnvelopeRow, original.envelope_id)
            envelope = (
                original
                if row is None
                else DomainEnvelope.model_validate(row.envelope_json)
            )
            revision = 0 if row is None else row.revision
        return DomainEnvelopeCheckpointRequest(
            project_key=_checkpoint_project_key(record, original),
            envelope=envelope,
            expected_revision=revision,
            execution_receipt=record.execution_receipt,
            document_id=self.document_id,
            adapter_key=record.adapter_key,
            source_extraction_result_id=record.extraction_result_id,
            source_payload_hash=domain_envelope_payload_hash(original),
            execution_context=(
                ExtractionExecutionContext.model_validate(
                    record.metadata["execution_context"]
                )
                if record.metadata.get("execution_context") is not None
                else None
            ),
            authenticated_groups=self.authenticated_groups,
        )


def refresh_saved_status(*, owner_id, session_id, saved_status):
    """Refresh owned saved-result state without invoking any model on replay."""
    if not saved_status:
        return None
    context = ChatReview(
        owner_id, saved_status["document_id"], session_id, "read-only", None, (), ""
    )
    current = context.status()
    requested = {item["result_ref"] for item in saved_status["results"]}
    items = [item for item in current["results"] if item["result_ref"] in requested]
    if {item["result_ref"] for item in items} != requested:
        raise store.HistoryAccessError("Saved review references are unavailable")
    return {
        **saved_status,
        **current,
        "results": items,
        "pending_count": sum(not item["ready"] for item in items),
    }


def refresh_saved_response(*, owner_id, session_id, status, original):
    """Replay current selected values without mutating the historical transcript."""
    if not status or status.get("presentation") != "selected_values":
        return original
    context = ChatReview(
        owner_id, status["document_id"], session_id, "read-only", None, (), ""
    )
    return context.selected_presentation(
        result_refs={item["result_ref"] for item in status["results"]}
    )
