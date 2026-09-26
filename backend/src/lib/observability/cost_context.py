"""Immutable, content-free execution identity for model cost attribution."""
from __future__ import annotations

import json
import logging
import os
import inspect
from functools import wraps
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Mapping
from uuid import uuid4

logger = logging.getLogger(__name__)
_context: ContextVar[str] = ContextVar("model_cost_context", default="{}")
_model_request: ContextVar[str] = ContextVar("model_request_identity", default="{}")
FIELDS = frozenset({
    "paper", "paper_category", "related_papers", "document_id", "artifact_revision",
    "run_id", "workflow_id", "node_id", "job_id", "activity", "environment", "deployment",
})


def current_cost_context() -> dict[str, Any]:
    return json.loads(_context.get())


def set_cost_context(context: Mapping[str, Any]):
    """Serialize a snapshot so mutable request objects cannot leak across tasks."""
    return _context.set(json.dumps({k: v for k, v in context.items() if k in FIELDS}))


@contextmanager
def cost_scope(context: Mapping[str, Any]):
    token = set_cost_context(context)
    try:
        yield
    finally:
        _context.reset(token)


def current_model_request() -> dict[str, Any]:
    return json.loads(_model_request.get())


@contextmanager
def model_request_scope(identity: Mapping[str, Any]):
    """Expose one measured model request to the tracing span its adapter opens.

    Held only while the provider adapter starts the attempt, so the SDK
    response/generation span records which measured request it carries.
    """
    token = _model_request.set(json.dumps({k: v for k, v in identity.items() if v is not None}))
    try:
        yield
    finally:
        _model_request.reset(token)


def execution_context(*, activity: str, document_id: str | None = None,
                      user_id: str | None = None, run_id: str | None = None,
                      workflow_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    """Resolve only an already verified, owned source-provider reference.

    Uploaded filenames, prompts, titles and prior conversation papers are never
    identity sources. Failure to resolve telemetry does not fail application work.
    """
    context = {
        "activity": activity, "run_id": run_id or str(uuid4()),
        "document_id": str(document_id) if document_id else None,
        "paper_category": "artifact_only" if document_id else "not_associated",
        "environment": os.getenv("SENTRY_ENVIRONMENT") or os.getenv("LANGFUSE_TRACING_ENVIRONMENT") or os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "unknown",
        "deployment": os.getenv("SENTRY_RELEASE") or os.getenv("GIT_SHA") or os.getenv("VITE_GIT_SHA") or None,
        "workflow_id": workflow_id, "job_id": job_id,
    }
    if not document_id or not user_id:
        return context
    try:
        from src.models.sql.database import SessionLocal
        from src.models.sql.pdf_document import PDFDocument
        from src.models.sql.user import User
        with SessionLocal() as db:
            document = db.query(PDFDocument).join(User, PDFDocument.user_id == User.id).filter(
                PDFDocument.id == document_id, User.auth_sub == user_id,
            ).one_or_none()
            if document is not None:
                reference = document.source_provider_reference_curie or document.source_provider_reference_id
                if document.source_provider and reference:
                    context["paper"] = {"namespace": document.source_provider, "id": reference}
                    context["paper_category"] = "paper"
                context["artifact_revision"] = document.file_hash or document.source_md5
    except Exception as exc:
        logger.warning("Paper cost attribution unavailable (%s)", type(exc).__name__)
    return context


def agent_identity(agent_id: str, name: str, category: str | None = None,
                   revision: str | None = None) -> dict[str, Any]:
    role = str(category or "unknown").strip().lower()
    if role == "output":
        role = "formatter"
    if role not in {"extraction", "validation", "chat", "supervisor", "formatter", "classifier", "other"}:
        role = "unknown"
    return {"agent_id": agent_id, "agent_name": name, "agent_role": role,
            "agent_revision": str(revision) if revision else None}


def costed_stream(function):
    """Scope one request execution, including async SDK child tasks and errors."""
    signature = inspect.signature(function)
    @wraps(function)
    async def wrapped(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        values = arguments.arguments
        agent = values.get("agent")
        context = getattr(agent, "cost_execution_context", None)
        state = values.get("state")
        if state is not None and hasattr(state, "cost_run_id"):
            context = execution_context(activity="authoring", run_id=state.cost_run_id)
        elif context is None:
            context = execution_context(
                activity="interactive_chat", document_id=values.get("document_id"),
                user_id=values.get("user_id"), run_id=values.get("turn_id"),
            )
        from src.lib.cost_ledger.runtime_context import RuntimeCostContext, runtime_cost_scope, child_invocation

        owner, session_id = values.get("user_id"), values.get("session_id")
        accounting_context = RuntimeCostContext(
            owner_subject=str(owner), session_id=str(session_id), run_id=str(context["run_id"]),
            activity=str(context["activity"]), workflow_id=context.get("workflow_id"),
            flow_run_id=context.get("job_id"),
            document_id=context.get("document_id"),
        ) if owner and session_id and context.get("run_id") else None
        from src.lib.openai_agents.provider_usage import has_provider_invocation_observer
        if has_provider_invocation_observer() or values.get("surface") == "benchmark_assistant":
            accounting_context = None
        accounting_context = child_invocation(accounting_context)
        stream = function(*args, **kwargs)
        try:
            while True:
                # Do not leave request context installed while the caller
                # processes a yielded event or advances another stream.
                with cost_scope(context), runtime_cost_scope(accounting_context):
                    try:
                        event = await anext(stream)
                    except StopAsyncIteration:
                        return
                yield event
        finally:
            with cost_scope(context), runtime_cost_scope(accounting_context):
                await stream.aclose()
    return wrapped


def get_agent_cost_identity(agent) -> dict[str, Any]:
    """Read identity from SDK-preserved hooks, including Sentry's Agent.clone()."""
    identity = getattr(getattr(agent, "hooks", None), "cost_identity", None)
    return dict(identity) if isinstance(identity, Mapping) else {}


def attach_agent_cost_identity(agent, identity: Mapping[str, Any]):
    """Attach registry identity to the SDK agent span without name classification."""
    from agents import AgentHooks
    from opentelemetry import trace

    identity = dict(identity)
    previous = getattr(agent, "hooks", None)

    class CostHooks(AgentHooks):
        @property
        def cost_identity(self):
            # Agent.clone() preserves hooks but drops dynamically attached agent
            # attributes. Keep one canonical identity here, not a second store.
            return dict(identity)

        async def on_start(self, context, running_agent):
            span = trace.get_current_span()
            if span.is_recording():
                metadata = {**current_cost_context(), **identity}
                span.set_attribute("metadata", json.dumps({"cost_context": metadata}))
            if previous is not None:
                await previous.on_start(context, running_agent)

        def __getattribute__(self, name):
            if name.startswith("on_") and name != "on_start" and previous is not None:
                return getattr(previous, name)
            return super().__getattribute__(name)

    agent.hooks = CostHooks()
    return agent


def costed_call(function):
    """Standalone/background calls inherit a parent run or create one boundary."""
    def context_for(agent):
        inherited = current_cost_context()
        if inherited.get("run_id"):
            return inherited
        identity = get_agent_cost_identity(agent)
        boundary = getattr(agent, "cost_boundary", {})
        return execution_context(
            activity="standalone_validation" if identity.get("agent_role") == "validation" else "background",
            document_id=boundary.get("document_id"), user_id=boundary.get("user_id"),
        )

    @contextmanager
    def scopes(agent):
        from src.lib.cost_ledger.runtime_context import current_runtime_cost_context, runtime_agent_scope
        context = context_for(agent)
        boundary = getattr(agent, "cost_boundary", {})
        accounting = current_runtime_cost_context() or runtime_context_for_boundary(
            context, owner_subject=boundary.get("user_id"),
        )
        with cost_scope(context), runtime_agent_scope(accounting, agent):
            yield

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(agent, *args, **kwargs):
            with scopes(agent):
                return await function(agent, *args, **kwargs)
        return asynchronous

    @wraps(function)
    def synchronous(agent, *args, **kwargs):
        with scopes(agent):
            return function(agent, *args, **kwargs)
    return synchronous


def runtime_context_for_boundary(context, *, owner_subject, session_id=None):
    """Build attribution only from a caller's trusted authenticated subject.

    Tracing metadata never supplies ownership. Database integer user IDs are
    deliberately rejected rather than being mistaken for auth subjects.
    """
    from src.lib.cost_ledger.runtime_context import RuntimeCostContext
    from src.lib.openai_agents.provider_usage import has_provider_invocation_observer
    if not isinstance(owner_subject, str) or not owner_subject or has_provider_invocation_observer():
        return None
    return RuntimeCostContext(
        owner_subject=owner_subject, session_id=session_id, run_id=context["run_id"],
        activity=context["activity"], workflow_id=context.get("workflow_id"),
        document_id=context.get("document_id"), job_id=context.get("job_id"),
    )


def costed_document_processing(function):
    """One background run for a known document pipeline, including classifiers."""
    signature = inspect.signature(function)
    @wraps(function)
    async def wrapped(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs).arguments
        request = arguments.get("request")
        context = current_cost_context()
        from src.lib.cost_ledger.runtime_context import current_runtime_cost_context, runtime_cost_scope
        accounting = current_runtime_cost_context()
        job_id = getattr(request, "job_id", None)
        owner = arguments.get("user_id") or getattr(request, "user_id", None)
        if job_id or not context.get("run_id"):
            context = execution_context(
                activity="background",
                document_id=arguments.get("document_id") or getattr(request, "document_id", None),
                user_id=owner,
                run_id=str(job_id) if job_id else None,
                job_id=str(job_id) if job_id else None,
            )
        if job_id or accounting is None:
            accounting = runtime_context_for_boundary(context, owner_subject=owner)
        with cost_scope(context), runtime_cost_scope(accounting):
            return await function(*args, **kwargs)
    return wrapped
