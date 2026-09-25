"""Shared per-item ``rationale`` contract for extraction builder tools.

Every extracting domain stores the extractor's own explanation of why it selected
each staged item, written at extraction time. Saved custom agents run from
frozen prompts, so the parameter description below is the complete guidance on
its own; every stage and patch tool uses it verbatim.
"""

from __future__ import annotations

from typing import Callable, TypeVar

RATIONALE_ARG_DESCRIPTION = (
    "Why you selected this item, in your own words. Shown to curators in review, "
    "CSV/TSV exports and chat."
)

_F = TypeVar("_F", bound=Callable[..., object])


def normalize_rationale(value: str) -> str:
    """Return the stripped rationale, or raise with an instruction the model can act on."""

    cleaned = value.strip()
    if not cleaned:
        raise ValueError("rationale must be non-empty: say why you selected this item")
    return cleaned


def document_rationale_arg(func: _F) -> _F:
    """Append the shared ``rationale`` entry to a tool impl's ``Args:`` docstring section.

    Apply before ``function_tool`` wraps the impl so the model-facing parameter
    description comes from one constant. The impl docstring must end with an
    ``Args:`` section indented by four spaces.
    """

    doc = func.__doc__ or ""
    if "\n    Args:\n" not in doc:
        raise ValueError(f"{func.__name__} docstring has no Args: section")
    func.__doc__ = doc.rstrip() + f"\n        rationale: {RATIONALE_ARG_DESCRIPTION}\n    "
    return func
