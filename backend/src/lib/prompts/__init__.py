"""Prompt versioning and caching module.

This module provides:
- Application-level prompt caching (zero DB queries at runtime)
- Prompt service for writes and execution logging
- ContextVar tracking for prompt usage audit

Usage:
    from src.lib.prompts.cache import get_prompt, get_prompt_by_version
    from src.lib.prompts.service import PromptService

    # Get active prompt (nanosecond lookup)
    prompt = get_prompt("pdf")  # Base prompt
    prompt = get_prompt("gene", mod_id="FB")  # MOD-specific rules

    # Get pinned version (for flows)
    prompt = get_prompt_by_version("gene", version=3)
"""

from .cache import (
    get_prompt,
    get_prompt_by_version,
    get_prompt_optional,
    get_cache_info,
    initialize,
    refresh,
    is_initialized,
    PromptNotFoundError,
)
from .context import (
    PromptOverride,
    set_prompt_override,
    get_prompt_override,
    clear_prompt_override,
    set_pending_prompts,
    commit_pending_prompts,
    get_used_prompts,
    clear_prompt_context,
    get_pending_for_agent,
)
from .models import PromptTemplate, PromptExecutionLog
from .service import PromptService

__all__ = [
    # Cache module
    "get_prompt",
    "get_prompt_by_version",
    "get_prompt_optional",
    "get_cache_info",
    "initialize",
    "refresh",
    "is_initialized",
    "PromptNotFoundError",
    # Context tracking
    "PromptOverride",
    "set_prompt_override",
    "get_prompt_override",
    "clear_prompt_override",
    "set_pending_prompts",
    "commit_pending_prompts",
    "get_used_prompts",
    "clear_prompt_context",
    "get_pending_for_agent",
    # Models
    "PromptTemplate",
    "PromptExecutionLog",
    # Service
    "PromptService",
]
