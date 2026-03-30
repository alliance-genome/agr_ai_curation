"""Adapter-owned field layout and metadata for reference curation drafts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping


REFERENCE_ADAPTER_KEY = "reference_adapter"

REFERENCE_TYPE_OPTIONS = (
    {"label": "Journal article", "value": "journal_article"},
    {"label": "Review article", "value": "review_article"},
    {"label": "Preprint", "value": "preprint"},
    {"label": "Book chapter", "value": "book_chapter"},
    {"label": "Other", "value": "other"},
)


@dataclass(frozen=True)
class ReferenceFieldDefinition:
    """Reference-adapter draft field specification."""

    field_key: str
    label: str
    field_type: str
    group_key: str
    group_label: str
    order: int
    required: bool = False
    default_value: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def metadata_payload(self) -> dict[str, Any]:
        """Return an isolated metadata payload for one prepared draft field."""

        return deepcopy(dict(self.metadata))


REFERENCE_FIELD_DEFINITIONS = (
    ReferenceFieldDefinition(
        field_key="citation.title",
        label="Title",
        field_type="string",
        group_key="citation_details",
        group_label="Citation details",
        order=0,
        required=True,
        metadata={
            "payload_path": "citation.title",
            "placeholder": "Full article title",
            "validation": {
                "rules": ["required", "non_empty_string"],
                "plan_key": "reference_field_plan_v1",
            },
            "evidence_display_priority": 10,
        },
    ),
    ReferenceFieldDefinition(
        field_key="citation.authors",
        label="Authors",
        field_type="json",
        group_key="citation_details",
        group_label="Citation details",
        order=10,
        default_value=[],
        metadata={
            "payload_path": "citation.authors",
            "widget": "reference_author_list",
            "helper_text": "One author per line.",
            "placeholder": "Ada Lovelace\nGrace Hopper",
            "validation": {
                "rules": ["list_of_strings"],
                "item_rule": "non_empty_string",
                "plan_key": "reference_field_plan_v1",
            },
            "evidence_display_priority": 20,
        },
    ),
    ReferenceFieldDefinition(
        field_key="citation.journal",
        label="Journal",
        field_type="string",
        group_key="citation_details",
        group_label="Citation details",
        order=20,
        metadata={
            "payload_path": "citation.journal",
            "placeholder": "Journal or source",
            "validation": {
                "rules": ["non_empty_string"],
                "plan_key": "reference_field_plan_v1",
            },
            "evidence_display_priority": 30,
        },
    ),
    ReferenceFieldDefinition(
        field_key="citation.publication_year",
        label="Publication year",
        field_type="number",
        group_key="citation_details",
        group_label="Citation details",
        order=30,
        metadata={
            "payload_path": "citation.publication_year",
            "validation": {
                "rules": ["integer", "publication_year"],
                "plan_key": "reference_field_plan_v1",
            },
            "evidence_display_priority": 40,
        },
    ),
    ReferenceFieldDefinition(
        field_key="citation.reference_type",
        label="Reference type",
        field_type="string",
        group_key="citation_details",
        group_label="Citation details",
        order=40,
        required=True,
        default_value="journal_article",
        metadata={
            "payload_path": "citation.reference_type",
            "options": list(REFERENCE_TYPE_OPTIONS),
            "validation": {
                "rules": ["required", "controlled_vocabulary"],
                "allowed_values": [option["value"] for option in REFERENCE_TYPE_OPTIONS],
                "plan_key": "reference_field_plan_v1",
            },
            "evidence_display_priority": 50,
        },
    ),
    ReferenceFieldDefinition(
        field_key="identifiers.doi",
        label="DOI",
        field_type="string",
        group_key="identifiers",
        group_label="Identifiers",
        order=100,
        metadata={
            "payload_path": "identifiers.doi",
            "placeholder": "10.1234/example.1",
            "validation": {
                "rules": ["doi_format"],
                "severity": "warning",
                "plan_key": "reference_field_plan_v1",
            },
            "evidence_display_priority": 60,
        },
    ),
    ReferenceFieldDefinition(
        field_key="identifiers.pmid",
        label="PMID",
        field_type="string",
        group_key="identifiers",
        group_label="Identifiers",
        order=110,
        metadata={
            "payload_path": "identifiers.pmid",
            "placeholder": "12345678",
            "validation": {
                "rules": ["pmid_format"],
                "severity": "warning",
                "plan_key": "reference_field_plan_v1",
            },
            "evidence_display_priority": 70,
        },
    ),
)

REFERENCE_FIELD_DEFINITIONS_BY_KEY = {
    field.field_key: field for field in REFERENCE_FIELD_DEFINITIONS
}
