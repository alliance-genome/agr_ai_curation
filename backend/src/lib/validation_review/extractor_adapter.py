"""Bind targeted follow-up to the original configured extractor and document."""
import asyncio

from src.lib.validation_history import store
from src.lib.validation_history import projection, selections
from . import admission, extraction_scheduling, extraction_execution


def followup_blocker(agent_key, contract) -> str | None:
    """Check saved follow-up structure without constructing an agent."""
    if not agent_key or not contract:
        return "saved_extractor_contract_unavailable"
    from .extractor_phase import TARGETED_EXTRACTION_PROTOCOL, TargetedFollowupTransport
    if (contract.get("followup_protocol") != TARGETED_EXTRACTION_PROTOCOL
            or contract.get("followup_transport_schema") != TargetedFollowupTransport.model_json_schema()):
        return "fresh_extraction_required"
    return None


class ExtractorAdapter:
    def __init__(self, context, artifact_id, *, authenticated_groups, checkpoint_provider, source_agent=None):
        self.context = context
        self.source_agent = source_agent
        self.artifact_id = artifact_id
        self.authenticated_groups = tuple(authenticated_groups)
        self.checkpoint_provider = checkpoint_provider
        baseline = store.read_baseline(context.history_scope, artifact_id)
        provenance = baseline["provenance"]
        self.receipt = {"agent_key": provenance.get("agent_key"),
                        "execution_receipt": provenance.get("execution_receipt"),
                        "contract": provenance.get("extractor_contract")}

    def resolve_agent(self, receipt):
        from src.lib.agent_studio.catalog_service import get_agent_by_id
        if receipt != self.receipt or not receipt["agent_key"] or not receipt["contract"]:
            raise admission.ReviewStopped("saved_extractor_contract_unavailable")
        if self.source_agent is not None:
            # Flow continuation uses the already resolved configured node; its
            # complete contract is checked again by extraction_execution.
            return self.source_agent
        from .extractor_phase import reconstruction_context
        saved_context = receipt["contract"].get("reconstruction_context", {})
        if reconstruction_context(saved_context) != saved_context:
            raise admission.ReviewStopped("saved_extractor_contract_unavailable")
        from src.lib.agent_studio.custom_profile_validators import runtime_validator_user_id
        kwargs = {**saved_context, "db_user_id": runtime_validator_user_id(self.context.history_scope.owner_id),
                  "user_id": self.context.history_scope.owner_id,
                  "document_id": self.context.history_scope.document_id,
                  "authenticated_groups": list(self.authenticated_groups)}
        pin = receipt["execution_receipt"]
        if pin is not None:
            kwargs.update(execution_revision_id=pin["agent_revision_id"], execution_receipt=pin)
        agent = get_agent_by_id(receipt["agent_key"], **kwargs)
        from src.lib.openai_agents.streaming_tools import _with_builder_finalization_guidance, is_builder_materializer_agent
        return _with_builder_finalization_guidance(agent) if is_builder_materializer_agent(agent) else agent

    async def request(self, *, operation_id, parent_decision_id, entry_ids, instructions):
        def prepare():
            blocker = followup_blocker(self.receipt["agent_key"], self.receipt["contract"])
            if blocker is not None:
                raise admission.ReviewStopped(blocker)
            checkpoint = self.checkpoint_provider()
            with store.SessionLocal.begin() as db:
                source = projection.source_envelope_in_transaction(
                    db, self.context.history_scope, self.artifact_id, checkpoint.envelope)
                source_sha = selections.source_state_digest(db, self.artifact_id)
            entries = {obj.metadata["validation_history"]["target_id"]: obj.model_dump(mode="json")
                       for obj in source.extracted_objects
                       if obj.metadata["validation_history"]["target_id"] in entry_ids}
            return extraction_scheduling.request(self.context, operation_id=operation_id,
                parent_decision_id=parent_decision_id, entry_ids=entry_ids, instructions=instructions,
                receipt=self.receipt, current_entries=entries, expected_source_sha=source_sha)

        work_id = await asyncio.to_thread(prepare)
        return await extraction_execution.execute(self.context.history_scope, work_id,
                                                  resolve_agent=self.resolve_agent)
