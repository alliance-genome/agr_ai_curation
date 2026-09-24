"""The subject kind builder tools accept for an annotation subject (disease, phenotype).

The subject kind picks the concrete annotation type and routes the subject check, which matches
it exactly, so a stage or patch tool accepts only these values and nothing normalizes them later.
"""

from __future__ import annotations

from typing import Literal, get_args

SubjectType = Literal["gene", "allele", "agm"]
SUBJECT_TYPES: tuple[str, ...] = get_args(SubjectType)
SUBJECT_TYPE_DESCRIPTION = (
    "What the subject is: exactly gene, allele or agm (a genetic model such as a strain or "
    "line). It picks the annotation type and which validator checks the subject."
)


def subject_type_issue(value: str | None) -> str | None:
    """Why a patched subject kind is not one of the accepted values, or None (a cleared kind is fine)."""

    cleaned = (value or "").strip()
    if cleaned and cleaned not in SUBJECT_TYPES:
        return f"subject_type must be exactly one of {', '.join(SUBJECT_TYPES)}"
    return None
