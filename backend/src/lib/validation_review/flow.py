"""Configured flow extraction + one shared supervisor review episode.

The step wrapper owns this context. Inline extraction capture hands its immutable
source here, so the later flow wrapper cannot start a second validator pipeline.
"""
import asyncio
from dataclasses import dataclass, field
from src.schemas.curation_workspace import CurationExtractionResultRecord as ExtractionRecord

from src.lib.validation_history import runtime, store
from . import admission, flow_plan, flow_steps
from .chat import ChatReview
from .validator_adapter import ValidatorAdapter, save_plan


@dataclass
class FlowReview(ChatReview):
    flow_run_id: str = ""
    step_id: str = ""
    node_id: str = ""
    validation_groups: list = field(default_factory=list)
    configured_nodes: dict = field(default_factory=dict)
    execution_context: dict = field(default_factory=dict)
    parent_scope_id: str | None = None
    record: ExtractionRecord | None = None
    source_agent: object | None = None
    initial_stop: BaseException | None = field(default=None, repr=False)

    def reference(self, record):
        return record.metadata["flow_review"]

    def operation_scope(self, scope):
        return admission.create_scope(scope,
            operation_id="flow:" + store.canonical({"run": self.flow_run_id,
                "session": self.session_id, "document": self.document_id})[0],
            kind="discovered_document_run", targets=0, episodes=0, parent_id=self.parent_scope_id)

    async def receive(self, envelope, domain_pack, *, agent_key, adapter_key, tool_name,
                      execution_receipt, context, trace_id=None, source_metadata=None,
                      builder_finalization=None, event_emitter=None, source_agent=None):
        from src.lib.curation_workspace.extraction_results import persist_inline_validated_extraction_result
        from src.schemas.curation_workspace import CurationExtractionSourceKind
        from src.lib.curation_workspace.models import CurationExtractionResultRecord
        from src.lib.curation_workspace.extraction_results import _record_to_schema

        if self.record is not None:
            raise store.HistoryConflict("Flow step cannot replace its extraction")
        binding = runtime.bind_artifact(envelope, context)
        plan = await asyncio.to_thread(flow_plan.prepare, envelope, domain_pack,
            groups=self.validation_groups, nodes=self.configured_nodes, runtime_context=context)
        plan.envelope.metadata["validation_review_runtime"] = "flow"
        plan_ref = await asyncio.to_thread(save_plan, plan, context=context)
        episode_id = plan.envelope.metadata["review_episode_id"]
        ref = {"artifact_id": binding.artifact_id, "episode_id": episode_id,
               "plan_ref": plan_ref, "history_scope_id": binding.scope.session_id,
               "operation_turn_id": self.turn_id, "step_id": self.step_id}
        saved = await asyncio.to_thread(persist_inline_validated_extraction_result,
            payload_json=plan.envelope.model_dump(mode="json"), execution_receipt=execution_receipt,
            document_id=self.document_id, agent_key=agent_key, adapter_key=adapter_key, tool_name=tool_name,
            source_kind=CurationExtractionSourceKind.FLOW, origin_session_id=self.session_id,
            user_id=self.owner_id, trace_id=trace_id, flow_run_id=self.flow_run_id,
            builder_finalization=builder_finalization,
            metadata={**(source_metadata or {}), "flow_review": ref, "flow_step_key": self.node_id,
                      "execution_context": self.execution_context})
        scope_id = await asyncio.to_thread(self.operation_scope, binding.scope)
        await asyncio.to_thread(admission.attach_episode, binding.scope, artifact_id=binding.artifact_id,
            episode_id=episode_id, scope_id=scope_id)
        await asyncio.to_thread(flow_steps.bind,
            store.HistoryScope(self.owner_id, self.document_id, self.session_id), self.step_id,
            artifact_id=binding.artifact_id, episode_id=episode_id, history_scope=binding.scope)

        def load():
            from uuid import UUID
            with store.SessionLocal() as db:
                row = db.get(CurationExtractionResultRecord, UUID(str(saved.extraction_result_id)))
                return _record_to_schema(row)
        self.record = await asyncio.to_thread(load)
        self.source_agent = source_agent
        adapter = ValidatorAdapter(binding.scope, binding.artifact_id, episode_id, plan_ref,
            authenticated_groups=self.authenticated_groups, event_emitter=event_emitter)
        try:
            await self.until_stop(adapter.initial())
        except Exception as error:
            from .stops import review_stop
            stopped = review_stop(error)
            if stopped is None:
                raise
            # Return the retained source through the streaming capture boundary,
            # then propagate the exact expected stop from the ordered review gate.
            self.initial_stop = stopped
        return plan.envelope.model_dump_json()

    def restore_source(self, claim):
        """Recover an inspectable reference without replaying extraction or review."""
        if claim["artifact_id"] is None:
            return
        from sqlalchemy import select
        from src.lib.curation_workspace.models import CurationExtractionResultRecord
        from src.lib.curation_workspace.extraction_results import _record_to_schema
        from uuid import UUID
        with store.SessionLocal() as db:
            store._authorize(db, store.HistoryScope(self.owner_id, self.document_id, self.session_id))
            rows = list(db.scalars(select(CurationExtractionResultRecord).where(
                CurationExtractionResultRecord.user_id == self.owner_id,
                CurationExtractionResultRecord.document_id == UUID(self.document_id),
                CurationExtractionResultRecord.origin_session_id == self.session_id,
                CurationExtractionResultRecord.flow_run_id == self.flow_run_id,
                CurationExtractionResultRecord.extraction_metadata["flow_review"]["artifact_id"].astext == claim["artifact_id"],
                CurationExtractionResultRecord.extraction_metadata["flow_review"]["episode_id"].astext == claim["episode_id"],
            )))
            if len(rows) != 1:
                raise store.HistoryConflict("Saved flow review source is unavailable or ambiguous")
            self.record = _record_to_schema(rows[0])

    def extractor_adapter(self, context, artifact_id, checkpoint):
        from .extractor_adapter import ExtractorAdapter
        return ExtractorAdapter(context, artifact_id, authenticated_groups=self.authenticated_groups,
            checkpoint_provider=checkpoint, source_agent=self.source_agent)

    async def finish_review(self):
        if self.initial_stop is not None:
            raise self.initial_stop
        if self.record is None:
            raise store.HistoryConflict("Flow extraction did not retain a reviewable source")
        outcome = await self.until_stop(self.review_result(self.record))
        if outcome is not None:
            await asyncio.to_thread(self.retain_review_outcome, self.record, outcome)
        ref = self.reference(self.record)
        context = self.context(self.record)
        status = await asyncio.to_thread(admission.status, context.history_scope, ref["episode_id"])
        if not status["ready"]:
            raise admission.ReviewStopped((outcome or {}).get("reason") or "flow_review_pending", resource=self.step_id)
        checkpoint = await asyncio.to_thread(self.checkpoint, self.record)
        result_id = self.record.extraction_result_id
        def retain_checkpoint():
            from uuid import UUID
            from src.lib.curation_workspace.models import CurationExtractionResultRecord
            from src.lib.curation_workspace.extraction_results import _record_to_schema
            with store.SessionLocal.begin() as db:
                row = db.get(CurationExtractionResultRecord, UUID(str(result_id)))
                row.extraction_metadata = {**row.extraction_metadata, "inspection_checkpoint": {
                    "envelope_id": checkpoint.envelope.envelope_id, "revision": checkpoint.expected_revision}}
                db.flush()
                return _record_to_schema(row)
        self.record = await asyncio.to_thread(retain_checkpoint)
        result = {"extraction_result_id": str(self.record.extraction_result_id),
                  "episode_id": ref["episode_id"], "checkpoint_revision": checkpoint.expected_revision}
        await asyncio.to_thread(flow_steps.finish,
            store.HistoryScope(self.owner_id, self.document_id, self.session_id), self.step_id, outcome=result)
        return checkpoint.envelope
