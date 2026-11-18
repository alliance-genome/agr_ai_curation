"""
Langfuse client initialization for OpenAI Agents SDK.

This module wires the Langfuse v4 OpenTelemetry exporter together with
OpenInference's OpenAI Agents SDK tracing processor. The Agents SDK emits one
hierarchical trace stream for agent spans, model calls, tool calls, handoffs,
guardrails, and errors; Langfuse exports those spans without wrapping the
OpenAI client itself.

Environment Variables Required:
    LANGFUSE_HOST: Langfuse server URL (e.g., http://langfuse:3000)
    LANGFUSE_PUBLIC_KEY: Langfuse project public key
    LANGFUSE_SECRET_KEY: Langfuse project secret key
"""

import os
import logging
from contextvars import ContextVar
from typing import List, Dict, Any, Optional

from src.lib.observability.langfuse import report_langfuse_failure

logger = logging.getLogger(__name__)

# Langfuse logs rejected credentials before raising or returning False. The
# caller owns that report. Keep unrelated SDK activity eligible for promotion.
_auth_check_active = ContextVar("langfuse_auth_check_active", default=False)


def _mark_auth_check_log(record: logging.LogRecord) -> bool:
    if _auth_check_active.get():
        record.sentry_skip_event = True
    return True


logging.getLogger("langfuse").addFilter(_mark_auth_check_log)

# Flag to track if we've logged the observe decorator fallback warning
_observe_fallback_warned = False


class OTELContextDetachFilter(logging.Filter):
    """
    Filter to suppress expected OTEL context detach errors in async generators.

    When using start_as_current_observation() with async generators, OTEL context can
    be created in one async task and the cleanup happens in another. This causes
    "Failed to detach context" errors that are non-fatal but noisy.

    The traces still work correctly - this just suppresses the error logging.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Suppress "Failed to detach context" errors from opentelemetry.context
        if record.name == "opentelemetry.context" and record.levelno == logging.ERROR:
            if "Failed to detach context" in str(record.getMessage()):
                # Log at DEBUG level for traceability before suppressing
                logger.debug(
                    "Suppressed expected OTEL context detach error: %s",
                    record.getMessage(),
                )
                return False  # Don't log this record
        return True  # Log all other records


# Apply the filter to the OTEL context logger to suppress expected errors
logging.getLogger("opentelemetry.context").addFilter(OTELContextDetachFilter())

# Langfuse configuration
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")

# Global Langfuse client instance
_langfuse_client = None
_openai_agents_instrumented = False

# Context-local storage for pending agent configs (async-safe)
# These are collected during agent creation and flushed to the trace later
# Using ContextVar instead of threading.local for proper async isolation
# Note: We don't use default=[] because that would share the same list across contexts
_pending_configs: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar('pending_configs', default=None)


def is_langfuse_configured() -> bool:
    """Check if Langfuse credentials are properly configured."""
    return bool(LANGFUSE_HOST and LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)


def initialize_langfuse():
    """
    Initialize Langfuse tracing.

    This configures Langfuse environment variables and initializes the client.
    Call this once at application startup.

    Returns:
        Langfuse client instance if successful, None otherwise
    """
    global _langfuse_client

    if not is_langfuse_configured():
        logger.info("Langfuse not configured")
        return None

    try:
        host = str(LANGFUSE_HOST)
        public_key = str(LANGFUSE_PUBLIC_KEY)
        secret_key = str(LANGFUSE_SECRET_KEY)

        # Set Langfuse environment variables for SDK initialization and OTLP export.
        os.environ["LANGFUSE_HOST"] = host
        os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
        os.environ["LANGFUSE_SECRET_KEY"] = secret_key

        # Use internal host for Docker container networking
        # The base URL without /api path for the SDK
        os.environ["LANGFUSE_BASEURL"] = host
        os.environ["OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA"] = "true"

        # Import and initialize Langfuse
        from langfuse import Langfuse

        # Create the global client directly (get_client() removed in 2.60.x)
        _langfuse_client = Langfuse(
            host=host,
            public_key=public_key,
            secret_key=secret_key,
        )
    except Exception as exc:
        report_langfuse_failure("initialization_failed", exc)
        return None

    if _langfuse_client is None:
        report_langfuse_failure("initialization_failed")
        return None

    _instrument_openai_agents_tracing()
    auth_token = _auth_check_active.set(True)
    try:
        if _langfuse_client.auth_check():
            logger.info("Langfuse initialized and authenticated")
        else:
            report_langfuse_failure("authentication_failed")
    except Exception as exc:
        report_langfuse_failure("authentication_failed", exc)
    finally:
        _auth_check_active.reset(auth_token)

    return _langfuse_client


def get_langfuse():
    """
    Get the initialized Langfuse client.

    Returns:
        Langfuse client instance if initialized, None otherwise
    """
    return _langfuse_client


def _instrument_openai_agents_tracing() -> None:
    """Install the OpenInference processor that exports Agents SDK traces to Langfuse."""
    global _openai_agents_instrumented

    if _openai_agents_instrumented:
        return

    try:
        from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

        OpenAIAgentsInstrumentor().instrument(exclusive_processor=True)
        from src.lib.observability.cost_tracing import install_cost_tracing
        install_cost_tracing()
        _openai_agents_instrumented = True
        logger.info("OpenAI Agents SDK tracing instrumented via OpenInference")
    except Exception as exc:
        report_langfuse_failure("instrumentation_failed", exc)


def is_openai_agents_tracing_enabled() -> bool:
    """Return whether Agents SDK spans are currently routed to Langfuse."""
    return _openai_agents_instrumented


def flush_langfuse():
    """
    Flush any pending Langfuse events.

    Call this at the end of a request to ensure all trace data is sent.
    """
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
        except Exception as exc:
            report_langfuse_failure("flush_failed", exc)


def create_trace(
    name: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list] = None
):
    """
    Create a new Langfuse trace manually.

    This is useful for wrapping operations that aren't automatically traced.

    Args:
        name: Name for the trace
        session_id: Optional session identifier
        user_id: Optional user identifier
        metadata: Optional metadata dict
        tags: Optional list of tags

    Returns:
        Langfuse trace object if configured, None otherwise
    """
    if _langfuse_client is None:
        return None

    try:
        trace = _langfuse_client.trace(
            name=name,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata or {},
            tags=tags or []
        )
        return trace
    except Exception as e:
        logger.warning("Failed to create Langfuse trace: %s", e)
        return None


def _get_pending_configs() -> List[Dict[str, Any]]:
    """Get the context-local list of pending configs.

    Creates a new list for this context if needed, ensuring proper isolation.
    """
    configs = _pending_configs.get()
    if configs is None:
        configs = []
        _pending_configs.set(configs)
    return configs


def clear_pending_configs():
    """Clear pending agent configs (call at start of each request).

    Sets a fresh empty list for this context.
    """
    _pending_configs.set([])


def log_agent_config(
    agent_name: str,
    instructions: str,
    model: str,
    tools: Optional[list] = None,
    model_settings: Optional[dict] = None,
    metadata: Optional[dict] = None
):
    """
    Queue agent configuration for later logging to Langfuse trace.

    Agent configs are collected during agent creation (before trace exists)
    and flushed to the trace later via flush_agent_configs().

    Args:
        agent_name: Name of the agent (e.g., "PDF Specialist")
        instructions: Full system prompt/instructions for the agent
        model: Model name (e.g., "gpt-6.1-sol")
        tools: List of tool names available to the agent
        model_settings: Dict with temperature, reasoning, etc.
        metadata: Additional metadata (document_id, hierarchy, etc.)
    """
    config = {
        "agent_name": agent_name,
        "instructions": instructions,
        "model": model,
        "tools": tools or [],
        "model_settings": model_settings or {},
        "metadata": metadata or {}
    }

    # Store config for later flushing
    pending = _get_pending_configs()
    pending.append(config)
    logger.info(
        "Queued config for agent: %s, model=%s, tools=%s (pending: %s)",
        agent_name,
        model,
        len(tools or []),
        len(pending),
    )


def flush_agent_configs(root_span) -> int:
    """
    Flush all pending agent configs to the Langfuse trace as EVENT observations.

    This should be called INSIDE the trace context (after start_as_current_observation()).
    Each config is logged as an event linked to the current trace.

    Args:
        root_span: The Langfuse span object from start_as_current_observation()

    Returns:
        Number of configs flushed
    """
    if _langfuse_client is None:
        logger.debug("Not configured, skipping agent config flush")
        clear_pending_configs()
        return 0

    pending = _get_pending_configs()
    if not pending:
        logger.debug("No pending agent configs to flush")
        return 0

    count = 0
    for config in pending:
        try:
            agent_name = config.get("agent_name", "Unknown")
            event_name = f"{agent_name.replace(' ', '_').lower()}_config"

            # Create an event linked to the current trace using trace_context
            # trace_context dict requires trace_id, parent_span_id is optional
            _langfuse_client.create_event(
                name=event_name,
                metadata={"agent_config": config},
                trace_context={
                    "trace_id": root_span.trace_id,
                    "parent_span_id": root_span.id
                }
            )
            count += 1
            logger.debug("Flushed config for agent: %s", agent_name)
        except Exception as e:
            logger.warning(
                "Failed to flush agent config for %s: %s",
                config.get("agent_name", "Unknown"),
                e,
            )

    # Clear the pending list
    clear_pending_configs()
    logger.info("Flushed %s agent configs to trace %s...", count, root_span.trace_id[:8])
    return count


# Export observe decorator for manual function tracing
try:
    from langfuse.decorators import observe
except ImportError:
    # Fallback no-op decorator if langfuse not installed
    def observe(*args, **kwargs):
        global _observe_fallback_warned
        if not _observe_fallback_warned:
            logger.warning(
                "langfuse package not installed - @observe decorator is a no-op. "
                "Install langfuse for function tracing: pip install langfuse"
            )
            _observe_fallback_warned = True

        def decorator(func):
            return func
        return decorator if not args or callable(args[0]) else decorator
