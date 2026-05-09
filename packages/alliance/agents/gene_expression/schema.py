"""Gene-expression extractor schema aliasing the shared domain-envelope output."""

from src.lib.openai_agents.models import (
    GeneExpressionEnvelope as RuntimeGeneExpressionEnvelope,
)


class GeneExpressionEnvelope(RuntimeGeneExpressionEnvelope):
    """Config-discovered alias for the runtime gene-expression extraction envelope."""

    __envelope_class__ = True
