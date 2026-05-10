"""Disease extractor schema for Alliance disease domain-envelope output."""

import sys
from pathlib import Path
from typing import Union

from pydantic import RootModel, model_validator

from src.lib.domain_packs.repair_patches import (
    DomainEnvelopeExtractorFinalClassification,
    DomainEnvelopeRepairPatch,
)
from src.lib.openai_agents.models import (
    DiseaseExtractionResultEnvelope as RuntimeDiseaseExtractionResultEnvelope,
)

_ALLIANCE_PYTHON_SRC = Path(__file__).resolve().parents[2] / "python" / "src"
if str(_ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(_ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    validate_disease_extraction_objects,
)


class DiseaseExtractionResultEnvelope(RuntimeDiseaseExtractionResultEnvelope):
    """Config-discovered Alliance disease extraction envelope."""

    __envelope_class__ = True

    @model_validator(mode="after")
    def _validate_disease_domain_contract(self) -> "DiseaseExtractionResultEnvelope":
        errors = validate_disease_extraction_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class DiseaseExtractorRepairResponse(
    RootModel[
        Union[
            DiseaseExtractionResultEnvelope,
            DomainEnvelopeRepairPatch,
            DomainEnvelopeExtractorFinalClassification,
        ]
    ]
):
    """Disease first-pass extraction or repair_action response schema."""

    __envelope_class__ = True
    __domain_envelope_extractor_repair_response__ = True
