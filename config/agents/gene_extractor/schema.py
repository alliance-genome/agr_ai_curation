"""Gene extractor schema aliasing runtime envelope for contract parity."""

from src.lib.openai_agents.models import (
    GeneExtractionResultEnvelope as RuntimeGeneExtractionResultEnvelope,
)


class GeneExtractionResultEnvelope(RuntimeGeneExtractionResultEnvelope):
    """Config-discovered alias for the runtime gene extraction envelope."""

    __envelope_class__ = True
