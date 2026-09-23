"""Shared per-item ``rationale`` contract for extraction builder tools.

Every extracting domain stores a short, curator-facing reason for each staged
item, written by the extractor at extraction time. Saved custom agents run from
frozen prompts, so the parameter description below is the complete guidance on
its own; every stage and patch tool uses it verbatim.
"""

from __future__ import annotations

from typing import Callable, TypeVar

RATIONALE_MAX_CHARS = 300

RATIONALE_ARG_DESCRIPTION = (
    "Curator-facing reason this item was selected, max 300 characters. Name the "
    "experiment, comparison, or statement that decides it, and why this "
    "term/entity/relation fits better than the nearest alternative (a broader/narrower "
    "term, a different allele or gene, another stage or tissue, a negated result). "
    "Ground it only in the recorded evidence. Do not repeat the quote or list the field "
    'values, do not open with "The paper states/shows", and do not describe your '
    "process. Shown in review, CSV/TSV and chat."
)

_F = TypeVar("_F", bound=Callable[..., object])


def normalize_rationale(value: str) -> str:
    """Return the stripped rationale, or raise with an instruction the model can act on."""

    cleaned = value.strip()
    if not cleaned:
        raise ValueError("rationale must be non-empty: give the curator-facing reason for this item")
    if len(cleaned) > RATIONALE_MAX_CHARS:
        raise ValueError(
            f"rationale is {len(cleaned)} characters; shorten it to at most "
            f"{RATIONALE_MAX_CHARS} characters and stage again"
        )
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
