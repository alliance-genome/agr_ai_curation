"""Disease extractor schema aliasing runtime envelope for contract parity."""

from src.lib.openai_agents.models import (
    DiseaseExtractionResultEnvelope as RuntimeDiseaseExtractionResultEnvelope,
)


class DiseaseExtractionResultEnvelope(RuntimeDiseaseExtractionResultEnvelope):
    """Config-discovered alias for the runtime disease extraction envelope."""

    __envelope_class__ = True
