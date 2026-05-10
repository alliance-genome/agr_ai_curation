"""Gene-expression extractor schema for Alliance domain-envelope output."""

import sys
from pathlib import Path
from typing import Union

from pydantic import RootModel, model_validator

from src.lib.domain_packs.repair_patches import (
    DomainEnvelopeExtractorFinalClassification,
    DomainEnvelopeRepairPatch,
)
from src.lib.openai_agents.models import (
    GeneExpressionEnvelope as RuntimeGeneExpressionEnvelope,
)

_ALLIANCE_PYTHON_SRC = Path(__file__).resolve().parents[2] / "python" / "src"
if str(_ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(_ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.gene_expression import (  # noqa: E402
    validate_gene_expression_extraction_objects,
)


class GeneExpressionEnvelope(RuntimeGeneExpressionEnvelope):
    """Config-discovered Alliance gene-expression extraction envelope."""

    __envelope_class__ = True

    @model_validator(mode="after")
    def _validate_gene_expression_domain_contract(self) -> "GeneExpressionEnvelope":
        errors = validate_gene_expression_extraction_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class GeneExpressionExtractorRepairResponse(
    RootModel[
        Union[
            GeneExpressionEnvelope,
            DomainEnvelopeRepairPatch,
            DomainEnvelopeExtractorFinalClassification,
        ]
    ]
):
    """Gene-expression first-pass extraction or repair_action response schema."""

    __envelope_class__ = True
    __domain_envelope_extractor_repair_response__ = True
