"""
Langfuse client initialization for OpenAI Agents SDK.

This module provides Langfuse observability integration using the drop-in
OpenAI replacement approach. All OpenAI calls made through the SDK are
automatically traced and sent to Langfuse.

Environment Variables Required:
    LANGFUSE_HOST: Langfuse server URL (e.g., http://langfuse:3000)
    LANGFUSE_PUBLIC_KEY: Langfuse project public key
    LANGFUSE_SECRET_KEY: Langfuse project secret key
"""

import os
import logging
from contextvars import ContextVar
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Flag to track if we've logged the observe decorator fallback warning
_observe_fallback_warned = False


class OTELContextDetachFilter(logging.Filter):
    """
    Filter to suppress expected OTEL context detach errors in async generators.

    When using start_as_current_span() with async generators, OTEL context can
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
        logger.warning(
            "Langfuse not configured. Set LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, "
            "and LANGFUSE_SECRET_KEY environment variables."
        )
        return None

    try:
        # Set Langfuse environment variables for the drop-in replacement
        os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST
        os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
        os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY

        # Use internal host for Docker container networking
        # The base URL without /api path for the SDK
        os.environ["LANGFUSE_BASEURL"] = LANGFUSE_HOST

        # Import and initialize Langfuse
        from langfuse import Langfuse

        # Create the global client directly (get_client() removed in 2.60.x)
        _langfuse_client = Langfuse(
            host=LANGFUSE_HOST,
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
        )

        # Test connection
        try:
            auth_result = _langfuse_client.auth_check()
            logger.info("Initialized and authenticated: %s", auth_result)
        except Exception as auth_error:
            logger.warning("Auth check failed (server may be starting): %s", auth_error)

        return _langfuse_client

    except ImportError:
        logger.error("langfuse package not installed. Run: pip install langfuse")
        return None
    except Exception as e:
        logger.error("Failed to initialize Langfuse: %s", e)
        return None


def get_langfuse():
    """
    Get the initialized Langfuse client.

    Returns:
        Langfuse client instance if initialized, None otherwise
    """
    return _langfuse_client


def flush_langfuse():
    """
    Flush any pending Langfuse events.

    Call this at the end of a request to ensure all trace data is sent.
    """
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
        except Exception as e:
            logger.warning("Failed to flush Langfuse: %s", e)


def create_trace(
    name: str,
    session_id: str = None,
    user_id: str = None,
    metadata: dict = None,
    tags: list = None
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
    tools: list = None,
    model_settings: dict = None,
    metadata: dict = None
):
    """
    Queue agent configuration for later logging to Langfuse trace.

    Agent configs are collected during agent creation (before trace exists)
    and flushed to the trace later via flush_agent_configs().

    Args:
        agent_name: Name of the agent (e.g., "PDF Specialist")
        instructions: Full system prompt/instructions for the agent
        model: Model name (e.g., "gpt-5-mini")
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

    This should be called INSIDE the trace context (after start_as_current_span).
    Each config is logged as an event linked to the current trace.

    Args:
        root_span: The Langfuse span object from start_as_current_span()

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
                input=config,
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
