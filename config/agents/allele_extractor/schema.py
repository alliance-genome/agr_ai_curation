"""Allele extractor schema aliasing runtime envelope for contract parity."""

from src.lib.openai_agents.models import (
    AlleleExtractionResultEnvelope as RuntimeAlleleExtractionResultEnvelope,
)


class AlleleExtractionResultEnvelope(RuntimeAlleleExtractionResultEnvelope):
    """Config-discovered alias for the runtime allele extraction envelope."""

    __envelope_class__ = True


# Backward-compatible alias for early draft references.
AlleleVariantExtractionEnvelope = AlleleExtractionResultEnvelope
