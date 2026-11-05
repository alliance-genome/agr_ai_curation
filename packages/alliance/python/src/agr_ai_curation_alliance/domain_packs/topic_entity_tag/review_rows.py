"""Show the server-captured paper reference on topic-entity-tag review rows."""

from __future__ import annotations

from dataclasses import dataclass

from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.schemas.curation_workspace import DomainEnvelopeReviewRow
from src.schemas.domain_envelope import DomainEnvelope


@dataclass(frozen=True)
class TopicEntityTagReviewRowMaterializer(DomainPackMetadataReviewRowMaterializer):
    """Read ``reference_curie`` from the document record captured when the run started.

    A paper uploaded with no ABC reference has no value, so the field stays blank and
    the tag cannot be submitted to ABC.
    """

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
        stored_envelope: DomainEnvelope | None = None,
    ) -> list[DomainEnvelopeReviewRow]:
        context = envelope.execution_context
        document = context.document if context is not None else None
        reference_curie = document.reference_curie if document is not None else None
        if not reference_curie:
            return super().materialize(
                envelope, envelope_revision=envelope_revision, stored_envelope=stored_envelope
            )
        display_envelope = envelope.model_copy(
            update={
                "extracted_objects": [
                    domain_object.model_copy(
                        update={"payload": {**domain_object.payload, "reference_curie": reference_curie}}
                    )
                    for domain_object in envelope.extracted_objects
                ]
            }
        )
        return super().materialize(
            display_envelope,
            envelope_revision=envelope_revision,
            stored_envelope=stored_envelope or envelope,
        )


__all__ = ["TopicEntityTagReviewRowMaterializer"]
