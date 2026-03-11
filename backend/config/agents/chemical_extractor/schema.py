"""Chemical extractor schema aliasing runtime envelope for contract parity."""

from src.lib.openai_agents.models import (
    ChemicalExtractionResultEnvelope as RuntimeChemicalExtractionResultEnvelope,
)


class ChemicalExtractionResultEnvelope(RuntimeChemicalExtractionResultEnvelope):
    """Config-discovered alias for the runtime chemical extraction envelope."""

    __envelope_class__ = True
