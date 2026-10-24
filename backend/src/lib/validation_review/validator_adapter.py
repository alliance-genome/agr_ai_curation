"""Trusted chat adapter: saved bindings, supplied-context validators and D work.

No agent identifier, profile or execution receipt is accepted from reviewer tools.
The immutable plan is recorded before first dispatch and every execution resolves
its binding from that plan, including saved custom validator revision pins.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from functools import partial
from dataclasses import asdict
from pydantic_core import to_jsonable_python

from pydantic import TypeAdapter

from src.lib.domain_packs.validator_dispatch import (
    ValidatorDispatchJob,
    ValidatorRuntimeContext,
    _dedupe_validator_jobs,
    _plan_validator_run_groups,
    _ValidatorRunGroup,
    _execute_validator_run_group_uncaptured,
    _default_package_scoped_validator_runner,
    _default_package_scoped_validator_batch_runner,
)
from src.lib.domain_packs.validation_registry import ValidatorBindingMatch
from src.lib.validation_history import store, runtime
from src.schemas.domain_validator import DomainValidationRequest
from . import admission, execution, scheduling
from .audit import append_fact

RECONSIDERATION_INPUT: ContextVar[dict | None] = ContextVar(
    "review_reconsideration_input", default=None
)
MATCH = TypeAdapter(ValidatorBindingMatch)


def binding_contract(match):
    """Pin executable definition and configured capability, not a catalog-wide grant."""
    from src.lib.config.agent_loader import get_agent_definition_for_package

    ref = match.binding.validator_agent
    if ref is None:
        raise store.HistoryConflict("Configured binding has no validator")
    definition = get_agent_definition_for_package(ref.package_id, ref.agent_id)
    if definition is None:
        raise store.HistoryConflict("Configured validator is unavailable")
    return store.canonical(
        {
            "binding": match.binding.raw,
            "agent": to_jsonable_python(asdict(definition)),
            "custom_validator": match.binding.raw.get("custom_validator"),
        }
    )[1]


def serialize_match(match):
    # Selector discriminators reject fields belonging to other selector kinds.
    # Preserve the complete envelope (including mutable metadata dictionaries),
    # while omitting unset selector options exactly as the registry does.
    value = MATCH.dump_python(match, mode="json")
    value["binding"]["input_fields"] = {
        key: selector.model_dump(mode="json", exclude_none=True)
        for key, selector in match.binding.input_fields.items()
    }
    if match.binding.routes is not None:
        for key, route in match.binding.routes.items():
            value["binding"]["routes"][key]["input_fields"] = {
                name: selector.model_dump(mode="json", exclude_none=True)
                for name, selector in route.input_fields.items()
            }
    return value


def save_plan(plan, *, context):
    binding = plan.history_binding
    matches = {}
    for match in plan.matches:
        tid = runtime.binding_target(binding, match)
        matches[tid] = serialize_match(match)
    body = {
        "envelope": plan.envelope.model_dump(mode="json"),
        "matches": matches,
        "contracts": {
            match.binding.binding_id: binding_contract(match)
            for match in plan.matches
            if match.binding.validator_agent is not None
        },
        "initial_requests": runtime.frozen_job_requests(binding, plan.initial_jobs),
        "authenticated_groups": list(context.authenticated_groups or ()),
    }
    with store.SessionLocal.begin() as db:
        store._artifact(db, binding.artifact_id, binding.scope)
        fact = append_fact(
            db,
            binding.artifact_id,
            operation_id="chat-review-plan:"
            + plan.envelope.metadata["review_episode_id"],
            kind="review_chat_plan",
            parent_id=plan.envelope.metadata["review_episode_id"],
            body=body,
        )
        return fact.id


class ValidatorAdapter:
    def __init__(
        self,
        scope,
        artifact_id,
        episode_id,
        plan_ref,
        *,
        authenticated_groups,
        event_emitter=None,
    ):
        self.event_emitter = event_emitter
        self.scope, self.artifact_id, self.episode_id = scope, artifact_id, episode_id
        self.plan_ref = plan_ref
        saved = store.read_fact(scope, artifact_id, plan_ref)
        if saved["kind"] != "review_chat_plan":
            raise store.HistoryConflict("Invalid saved review plan")
        self.plan = saved["content"]
        if self.plan["envelope"]["metadata"]["review_episode_id"] != episode_id:
            raise store.HistoryConflict("Review plan belongs to another episode")
        self.context = ValidatorRuntimeContext(
            document_id=scope.document_id,
            user_id=scope.owner_id,
            authenticated_groups=tuple(authenticated_groups),
        )
        self.binding = runtime.CapturedArtifact(scope, artifact_id)

    def emit(self, event):
        from src.lib.openai_agents.extraction_trace_events import (
            write_extraction_trace_event,
        )

        write_extraction_trace_event(
            event_type="validation_review.validator_progress",
            output_summary=event,
            metadata={"episode_id": self.episode_id},
        )
        if self.event_emitter is not None:
            self.event_emitter(event)
        elif event.get("event") in {
            "validator_batch_start",
            "validator_batch_complete",
        }:
            from src.lib.openai_agents.streaming_tools import add_specialist_event

            add_specialist_event(
                {
                    "type": "TOOL_START"
                    if event["event"] == "validator_batch_start"
                    else "TOOL_COMPLETE",
                    "details": {
                        "toolName": "dispatch_active_validator_batch",
                        "isSpecialistInternal": True,
                        "friendlyName": "Saved validator work",
                        **event,
                    },
                }
            )

    def match(self, target_id):
        if target_id not in self.plan["matches"]:
            raise store.HistoryAccessError(
                "Target is outside configured review bindings"
            )
        match = MATCH.validate_python(self.plan["matches"][target_id])
        required = match.binding.required_any_active_group
        if required and not set(required).intersection(
            self.context.authenticated_groups or ()
        ):
            raise admission.ReviewStopped("validator_not_authorized")
        return match

    def job(self, frozen):
        match = self.match(frozen["target_id"])
        request = DomainValidationRequest.model_validate(frozen["request"])
        if (
            match.binding.validator_agent is None
            or frozen["binding_id"] != match.binding.binding_id
            or request.validator_binding_id != match.binding.binding_id
            or request.validator_agent.model_dump(mode="json")
            != match.binding.validator_agent.to_dict()
            or frozen["review_episode_id"] != self.episode_id
        ):
            raise store.HistoryAccessError(
                "Validator dispatch differs from configured binding"
            )
        return ValidatorDispatchJob(match, request, history_episode_id=self.episode_id)

    def receipt(self, jobs, *, batch_key):
        contracts = {
            job.match.binding.binding_id: self.plan["contracts"][
                job.match.binding.binding_id
            ]
            for job in jobs
        }
        return {
            "plan_ref": self.plan_ref,
            "contracts": contracts,
            "batch_key": batch_key,
        }

    async def initial(self):
        frozen = self.plan["initial_requests"]

        def scheduled_targets():
            from sqlalchemy import select
            from src.models.sql.validation_review import ValidationReviewWork

            with store.SessionLocal() as db:
                store._artifact(db, self.artifact_id, self.scope)
                rows = db.scalars(
                    select(ValidationReviewWork).where(
                        ValidationReviewWork.episode_id == self.episode_id
                    )
                )
                return {
                    item["target_id"]
                    for row in rows
                    for item in row.payload["requests"]
                }

        scheduled = await asyncio.to_thread(scheduled_targets)
        jobs = [self.job(item) for item in frozen if item["target_id"] not in scheduled]
        from src.lib.domain_packs.validator_dispatch import (
            preflight_unresolved_validator_result,
        )

        executable = []
        for job in jobs:
            blocked = preflight_unresolved_validator_result(
                job.request, policy=job.match.binding.preflight_policy
            )
            if blocked is None:
                executable.append(job)
            else:
                # Missing configured lookup inputs are a structural scheduling gap,
                # never a fabricated scientific answer from a validator.
                await asyncio.to_thread(
                    store.record_scheduling,
                    self.scope,
                    self.artifact_id,
                    operation_id="chat-preflight:"
                    + runtime.request_target(self.binding, job),
                    outcome="unsupported_preflight",
                    details={"binding_id": job.match.binding.binding_id},
                )
        jobs = executable
        by_target = {item["target_id"]: item for item in frozen}
        grouped = _dedupe_validator_jobs(jobs)
        work_ids = []
        for group in _plan_validator_run_groups(grouped):
            selected = [
                job for index in group.dedupe_group_indexes for job in grouped[index]
            ]
            requests = [
                by_target[runtime.request_target(self.binding, job)] for job in selected
            ]
            op = (
                "initial:"
                + store.canonical({"targets": [r["target_id"] for r in requests]})[0]
            )
            wid = await asyncio.to_thread(
                scheduling.initial,
                self.scope,
                self.episode_id,
                operation_id=op,
                requests=requests,
                receipt=self.receipt(selected, batch_key=group.batch_key),
            )
            work_ids.append(wid)
        # Existing configured validator concurrency applies; no new operational cap.
        from src.lib.openai_agents.config import get_max_parallel_validators

        semaphore = asyncio.Semaphore(get_max_parallel_validators())

        async def run(wid):
            async with semaphore:
                return await self.execute(wid)

        # A reviewer may have queued reconsideration just before disconnecting.
        # Recover all episode work, not only the initial scheduling operations.
        def pending_work():
            from sqlalchemy import select
            from src.models.sql.validation_review import ValidationReviewWork

            with store.SessionLocal() as db:
                store._artifact(db, self.artifact_id, self.scope)
                return list(
                    db.scalars(
                        select(ValidationReviewWork.id).where(
                            ValidationReviewWork.episode_id == self.episode_id
                        )
                    )
                )

        work_ids = await asyncio.to_thread(pending_work)
        return await asyncio.gather(*(run(wid) for wid in work_ids))

    async def execute(self, work_id):
        from . import work

        await asyncio.to_thread(work.recover, self.scope, work_id)
        return await execution.execute(self.scope, work_id, call=self.call)

    async def call(self, payload):
        receipt = payload["receipt"]
        if receipt["plan_ref"] != self.plan_ref:
            raise store.HistoryAccessError("Work belongs to another configured plan")
        jobs = [self.job(item) for item in payload["requests"]]
        if any(
            receipt["contracts"].get(key) != value
            for key, value in self.receipt(jobs, batch_key=receipt["batch_key"])[
                "contracts"
            ].items()
        ):
            raise admission.ReviewStopped("configured_validator_changed")
        try:
            changed = any(
                binding_contract(job.match)
                != receipt["contracts"][job.match.binding.binding_id]
                for job in jobs
            )
        except store.HistoryConflict as error:
            raise admission.ReviewStopped("configured_validator_unavailable") from error
        if changed:
            raise admission.ReviewStopped("configured_validator_changed")
        # Custom revisions are reauthorized by the existing package runner.
        return await asyncio.to_thread(self._call, payload, jobs)

    def _call(self, payload, jobs):
        grouped = _dedupe_validator_jobs(jobs)
        run_group = _ValidatorRunGroup(
            tuple(range(len(grouped))), payload["receipt"]["batch_key"]
        )
        active = runtime.ACTIVE_VALIDATION_EXECUTION.set(
            (self.binding, payload["invocation"])
        )
        followup = RECONSIDERATION_INPUT.set(
            {
                "protocol": payload["reconsideration_protocol"],
                "context": payload["followup"],
            }
            if payload["followup"] is not None
            else None
        )
        try:
            outcomes = []
            groups = (
                [run_group]
                if run_group.batch_key is not None
                else [_ValidatorRunGroup((i,)) for i in range(len(grouped))]
            )
            for current in groups:
                result = _execute_validator_run_group_uncaptured(
                    current,
                    grouped_jobs=grouped,
                    agent_runner=partial(
                        _default_package_scoped_validator_runner(),
                        runtime_context=self.context,
                    ),
                    batch_runner=partial(
                        _default_package_scoped_validator_batch_runner(),
                        runtime_context=self.context,
                    ),
                    event_emitter=self.emit,
                )
                answers = {
                    job.request.request_id: answer
                    for index, answers_for_group in result.dedupe_group_results.items()
                    for job, answer in zip(
                        grouped[index], answers_for_group, strict=True
                    )
                }
                outcomes.extend(self._retain_answers(payload, jobs, answers))
        finally:
            RECONSIDERATION_INPUT.reset(followup)
            runtime.ACTIVE_VALIDATION_EXECUTION.reset(active)
        return outcomes

    def _retain_answers(self, payload, jobs, answers):
        outcomes = []
        for attempt_id, job in zip(
            payload["invocation"]["attempt_ids"], jobs, strict=True
        ):
            if job.request.request_id not in answers:
                continue
            answer = answers[job.request.request_id]
            failed = answer._execution_failure is not None
            state = (
                "failed"
                if failed
                else "incomplete"
                if answer.output_issues
                else "completed"
            )
            normalized = answer.model_dump(mode="json")
            from src.lib.domain_packs.validator_dispatch import (
                _with_returned_optional_fields,
            )
            from src.lib.domain_packs.materialization import (
                ValidatorResultMaterializationInput,
            )

            item = _with_returned_optional_fields(
                ValidatorResultMaterializationInput(job.match, job.request, answer)
            )
            candidate = (
                None
                if failed
                else {
                    "binding_fingerprint": store.canonical(
                        {
                            "binding": job.match.binding.raw,
                            "object_definition": job.match.object_definition.model_dump(
                                mode="json"
                            )
                            if job.match.object_definition is not None
                            else None,
                        }
                    )[0],
                    "request": item.request.model_dump(mode="json"),
                    "result": normalized,
                    "source_envelope_revision": 1,
                }
            )
            outcomes.append(
                execution.ValidatorOutcome(
                    attempt_id,
                    state,
                    None
                    if failed
                    else answer._raw_output
                    if answer._raw_output is not None
                    else normalized,
                    {"failure_kind": answer._execution_failure} if failed else {},
                    candidate,
                )
            )
        # This synchronous adapter can finish after a cancelled awaiting task.
        # Retain actual late answers atomically; the executor repeats this write
        # idempotently before completing its leased work record.
        for outcome in outcomes:
            store.complete_attempt(
                self.scope,
                self.artifact_id,
                outcome.attempt_id,
                state=outcome.state,
                result=outcome.result,
                diagnostics=outcome.diagnostics,
                candidate=outcome.candidate,
            )
        from types import SimpleNamespace
        from src.lib.openai_agents.streaming_tools import (
            _emit_validator_lookup_audit_events,
        )

        _emit_validator_lookup_audit_events(
            specialist_name="Saved validation review",
            dispatch_result=SimpleNamespace(validator_results=list(answers.values())),
        )
        return outcomes

    def prepare_inputs(self, checkpoint_provider):
        """Snapshot current selected inputs outside the queue transaction."""
        from src.lib.validation_history import selections
        from src.models.sql.validation_history import ValidationFact
        from sqlalchemy import select

        checkpoint = checkpoint_provider()
        with store.SessionLocal.begin() as db:
            manifest = selections.manifest_in_transaction(
                db, self.scope, self.artifact_id, episode_id=self.episode_id
            )
            reference = checkpoint.envelope.metadata.get("selected_projection")
            if reference is not None:
                from src.lib.validation_history.projection import (
                    read_published_projection_in_transaction,
                )

                selected_envelope, manifest = read_published_projection_in_transaction(
                    db, self.scope, reference, current=True
                )
            else:
                if any(unit["selected"] is not None for unit in manifest["units"]):
                    raise store.HistoryConflict(
                        "Selected input checkpoint is unavailable"
                    )
                selected_envelope = checkpoint.envelope
            sources = {
                unit["target_id"]: store._content(
                    db,
                    store._fact(
                        db, self.artifact_id, unit["selected"], kind="completion"
                    ),
                )
                for unit in manifest["units"]
                if unit["eligible"]
                and unit["selected"] not in {None, "baseline", "curator"}
            }
            attempted = {
                row.target_id
                for row in db.scalars(
                    select(ValidationFact).where(
                        ValidationFact.artifact_id == self.artifact_id,
                        ValidationFact.kind == "attempt",
                    )
                )
                if store._content(db, row)["input"]["review_episode_id"]
                == self.episode_id
            }
            parents = {
                row.id: row.parent_id
                for row in db.scalars(
                    select(ValidationFact).where(
                        ValidationFact.artifact_id == self.artifact_id,
                        ValidationFact.kind == "target",
                    )
                )
            }
        self.target_parents = parents
        self.selected_envelope = selected_envelope
        self.selected_manifest = manifest
        self.selected_sources = sources
        self.attempted_targets = attempted

    def selected_request(self, target_id, sources):
        """Pure selector callback. Changed selection invalidates the prepared snapshot."""
        from dataclasses import replace
        from src.lib.domain_packs.input_selectors import build_domain_validation_request

        if any(self.selected_sources.get(tid) != body for tid, body in sources.items()):
            raise store.HistoryConflict(
                "Selected prerequisites changed; refresh the review packet"
            )
        match = self.match(target_id)
        obj = None
        if match.object_envelope is not None:
            identity = match.object_envelope.metadata["validation_history"]["target_id"]
            found = [
                obj
                for obj in self.selected_envelope.extracted_objects
                if obj.metadata["validation_history"]["target_id"] == identity
            ]
            if len(found) != 1:
                raise store.HistoryConflict("Dependent validator source target changed")
            obj = found[0]
        selected = replace(match, envelope=self.selected_envelope, object_envelope=obj)
        return build_domain_validation_request(selected).request

    def refresh_inputs(self, frozen_request, sources):
        found = []
        for tid in self.plan["matches"]:
            if (
                self.match(tid).binding.binding_id
                != frozen_request["validator_binding_id"]
            ):
                continue
            request = self.selected_request(tid, sources)
            if (
                request is not None
                and request.target.model_dump(mode="json") == frozen_request["target"]
            ):
                found.append(request)
        if len(found) != 1:
            raise store.HistoryConflict("Reconsideration has no unique saved target")
        return found[0].selected_inputs

    async def advance(self, checkpoint_provider):
        """Only configured dependent initial work may follow explicit selections."""
        from . import waves

        await asyncio.to_thread(self.prepare_inputs, checkpoint_provider)
        outcomes = []
        for tid in self.plan["matches"]:
            match = self.match(tid)
            if not match.binding.runs_after or tid in self.attempted_targets:
                continue
            dependencies = []
            for other_id in self.plan["matches"]:
                other = self.match(other_id)
                same_object = (
                    match.object_envelope is None
                    or other.object_envelope is None
                    or match.object_envelope.metadata["validation_history"]["target_id"]
                    == other.object_envelope.metadata["validation_history"]["target_id"]
                )
                if same_object and other.binding.binding_id in match.binding.runs_after:
                    dependencies.extend(
                        unit["target_id"]
                        for unit in self.selected_manifest["units"]
                        if unit["target_id"] == other_id
                        or self.target_parents[unit["target_id"]] == other_id
                    )
            if not dependencies:
                outcomes.append(
                    {
                        "state": "not_runnable",
                        "target": tid,
                        "reason": "required_prerequisites_unavailable",
                    }
                )
                continue
            request = self.selected_request(tid, self.selected_sources)
            frozen = (
                []
                if request is None
                else runtime.frozen_job_requests(
                    self.binding,
                    [
                        ValidatorDispatchJob(
                            match, request, history_episode_id=self.episode_id
                        )
                    ],
                )
            )

            def build(sources):
                # All identity registration happened above, outside the locked
                # admission transaction. This callback is deliberately pure.
                if any(
                    self.selected_sources.get(key) != value
                    for key, value in sources.items()
                ):
                    raise store.HistoryConflict("Prerequisite selection changed")
                return frozen

            receipt = {
                "plan_ref": self.plan_ref,
                "contracts": {
                    match.binding.binding_id: self.plan["contracts"][
                        match.binding.binding_id
                    ]
                },
                "batch_key": None,
            }
            scheduled = await asyncio.to_thread(
                waves.advance,
                self.scope,
                self.episode_id,
                operation_id="dependent-initial:" + tid,
                prerequisite_targets=dependencies,
                receipt=receipt,
                build_requests=build,
            )
            if scheduled["state"] == "queued":
                scheduled = await self.execute(scheduled["work_id"])
            outcomes.append(scheduled)
        return outcomes


def pin_validator_agent(agent, binding_id):
    """Refuse changed executable model/prompt/tool contracts before provider submission."""
    active = runtime.ACTIVE_VALIDATION_EXECUTION.get()
    from .provider import ACTIVE_BUDGET

    budget = ACTIVE_BUDGET.get()
    if active is None or budget is None:
        return
    binding, invocation = active
    from src.lib.observability.cost_context import get_agent_cost_identity
    from .admission import ReviewStopped

    if not isinstance(agent.instructions, str):
        raise ReviewStopped("unresolved_validator_instructions")
    contract = to_jsonable_python(
        {
            "model": agent.model
            if isinstance(agent.model, str)
            else getattr(agent.model, "model", None),
            "settings": asdict(agent.model_settings),
            "instructions": agent.instructions,
            "identity": get_agent_cost_identity(agent),
            "execution_receipt": getattr(agent, "execution_receipt", None),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.params_json_schema,
                }
                for tool in agent.tools
            ],
        }
    )
    with store.SessionLocal.begin() as db:
        store._artifact(db, binding.artifact_id, binding.scope)
        store._fact(
            db, binding.artifact_id, invocation["execution_id"], kind="execution"
        )
        requests = [
            store._content(
                db, store._fact(db, binding.artifact_id, aid, kind="attempt")
            )["input"]
            for aid in invocation["attempt_ids"]
        ]
        # The budget binds the exact admitted episode; no model can change this.
        if not any(request["binding_id"] == binding_id for request in requests):
            raise store.HistoryAccessError("Agent is outside the admitted invocation")
        operation = "chat-validator-config:" + budget.episode_id + ":" + binding_id
        from sqlalchemy import select
        from src.models.sql.validation_history import ValidationFact

        existing = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == binding.artifact_id,
                ValidationFact.operation_id == operation,
            )
        )
        if existing is not None and store._content(db, existing) != contract:
            raise ReviewStopped("configured_validator_changed")
        append_fact(
            db,
            binding.artifact_id,
            operation_id=operation,
            kind="review_validator_config",
            body=contract,
        )
