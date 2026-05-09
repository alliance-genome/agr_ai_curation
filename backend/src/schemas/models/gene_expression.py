"""Gene expression domain-envelope schema.

New gene-expression extractor runs use ``curatable_objects[]`` as the only
semantic object list. Raw mentions, evidence, exclusions, ambiguities, notes,
and repair details live under envelope metadata.
"""

from .domain_envelope_extraction import DomainEnvelopeExtractionResult


class GeneExpressionEnvelope(DomainEnvelopeExtractionResult):
    """Envelope for gene-expression domain-envelope extraction responses."""
