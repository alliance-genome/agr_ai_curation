"""LangSmith observability service."""

import os
import asyncio
from functools import wraps
from typing import Any, Dict, Optional
from uuid import UUID

from app.config import get_settings


class LangSmithService:
    """Manages LangSmith tracing configuration."""

    _initialized = False

    @classmethod
    def initialize(cls) -> None:
        """Initialize LangSmith tracing if configured."""
        if cls._initialized:
            return

        settings = get_settings()
        if not settings.langsmith_is_configured:
            print(
                "⚠️ LangSmith not configured (set LANGSMITH_API_KEY and LANGSMITH_ENABLED=true)"
            )
            return

        # Set environment variables for LangChain/LangGraph
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
        os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"

        # Set sampling rate for production
        if settings.langsmith_tracing_sampling_rate < 1.0:
            os.environ["LANGCHAIN_TRACING_SAMPLING_RATE"] = str(
                settings.langsmith_tracing_sampling_rate
            )

        cls._initialized = True
        print(f"✅ LangSmith tracing enabled for project: {settings.langsmith_project}")
        print(f"   Sampling rate: {settings.langsmith_tracing_sampling_rate * 100}%")

    @classmethod
    def add_metadata_to_current_trace(cls, metadata: Dict[str, Any]) -> None:
        """Add metadata to the current LangSmith trace."""
        if not cls._initialized:
            return

        try:
            from langsmith import get_current_run_tree

            run_tree = get_current_run_tree()
            if run_tree:
                run_tree.metadata.update(metadata)
        except ImportError:
            pass
        except Exception:
            pass  # Fail silently in production


def with_langsmith_metadata(**metadata):
    """Decorator to add metadata to LangSmith traces."""

    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            LangSmithService.add_metadata_to_current_trace(metadata)
            return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            LangSmithService.add_metadata_to_current_trace(metadata)
            return func(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# Initialize on module import
LangSmithService.initialize()
