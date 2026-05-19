"""Gene-expression extractor schema for Alliance domain-envelope output."""

import copy
import re
import sys
from pathlib import Path
from collections.abc import Mapping

from pydantic import model_validator
from src.lib.openai_agents.models import (
    GeneExpressionEnvelope as RuntimeGeneExpressionEnvelope,
)

_ALLIANCE_PYTHON_SRC = Path(__file__).resolve().parents[2] / "python" / "src"
if str(_ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(_ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.gene_expression import (  # noqa: E402
    validate_gene_expression_extraction_objects,
)
from agr_ai_curation_alliance.domain_packs.gene_expression.constants import (  # noqa: E402
    GENE_EXPRESSION_LINKML_SCHEMA_ID,
    GENE_EXPRESSION_LINKML_SCHEMA_NAME,
    GENE_EXPRESSION_LINKML_SCHEMA_URI,
    GENE_EXPRESSION_MODEL_ID,
    GENE_EXPRESSION_OBJECT_TYPE,
)
from agr_ai_curation_alliance.domain_packs.schema_refs import (  # noqa: E402
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _slug(value: object, *, fallback: str) -> str:
    text = _optional_text(value) or fallback
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or fallback


def _next_pending_ref(base: str, used_refs: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used_refs:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_refs.add(candidate)
    return candidate


def _object_ref_value(obj: Mapping[str, object]) -> str | None:
    return _optional_text(obj.get("pending_ref_id")) or _optional_text(
        obj.get("object_id")
    )


def _gene_expression_schema_ref() -> dict[str, object]:
    return {
        "schema_id": GENE_EXPRESSION_LINKML_SCHEMA_ID,
        "provider": ALLIANCE_LINKML_PROVIDER_KEY,
        "name": GENE_EXPRESSION_LINKML_SCHEMA_NAME,
        "version": ALLIANCE_LINKML_COMMIT,
        "uri": GENE_EXPRESSION_LINKML_SCHEMA_URI,
    }


class GeneExpressionEnvelope(RuntimeGeneExpressionEnvelope):
    """Config-discovered Alliance gene-expression extraction envelope."""

    __envelope_class__ = True

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_gene_expression_scaffold(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value

        normalized = copy.deepcopy(dict(value))
        curatable_objects = normalized.get("curatable_objects")
        if not isinstance(curatable_objects, list):
            return normalized

        used_refs = {
            ref
            for obj in curatable_objects
            if isinstance(obj, Mapping)
            for ref in [_object_ref_value(obj)]
            if ref
        }
        for index, obj in enumerate(curatable_objects):
            if not isinstance(obj, dict):
                continue
            if obj.get("object_type") != GENE_EXPRESSION_OBJECT_TYPE:
                continue
            if not _object_ref_value(obj):
                payload = obj.get("payload")
                gene_symbol = (
                    payload.get("expression_annotation_subject", {}).get("gene_symbol")
                    if isinstance(payload, Mapping)
                    and isinstance(
                        payload.get("expression_annotation_subject"), Mapping
                    )
                    else None
                )
                base = (
                    "gene-expression-annotation-"
                    f"{_slug(gene_symbol, fallback=str(index + 1))}"
                )
                obj["pending_ref_id"] = _next_pending_ref(base, used_refs)
            obj.setdefault("model_ref", GENE_EXPRESSION_MODEL_ID)
            obj.setdefault("schema_ref", _gene_expression_schema_ref())

        return normalized

    @model_validator(mode="after")
    def _validate_gene_expression_domain_contract(self) -> "GeneExpressionEnvelope":
        errors = validate_gene_expression_extraction_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self
