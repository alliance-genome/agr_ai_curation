"""Phenotype extractor schema aliasing the shared domain-envelope output."""

from src.lib.openai_agents.models import (
    PhenotypeResultEnvelope as RuntimePhenotypeResultEnvelope,
)


class PhenotypeResultEnvelope(RuntimePhenotypeResultEnvelope):
    """Config-discovered alias for the runtime phenotype extraction envelope."""

    __envelope_class__ = True
