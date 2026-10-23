"""Workspace visibility of independently committed manual source preparations."""
from sqlalchemy import exists, or_, select
from src.lib.curation_workspace.models import CurationCandidate, DomainEnvelopeModel


def linked_or_non_prepared_envelope():
    """A prepared source becomes a workspace record only through committed linkage."""
    return or_(
        DomainEnvelopeModel.envelope_json["metadata"]["manual_preparation"].as_string().is_(None),
        exists(select(CurationCandidate.id).where(
            CurationCandidate.envelope_id == DomainEnvelopeModel.envelope_id,
            CurationCandidate.session_id == DomainEnvelopeModel.session_id,
        )),
    )
