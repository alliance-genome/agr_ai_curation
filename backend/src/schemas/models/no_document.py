"""No document envelope schema.

Used when no document is available but one is needed.
"""

from typing import Optional
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope, Destination


class NoDocumentEnvelope(StructuredMessageEnvelope):
    """Envelope for responses when no document is available but one is needed"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="supervisor", description="The supervisor agent")
    destination: Destination = Field(default=Destination.NO_DOCUMENT_RESPONSE)
    message: str = Field(
        default="Please load a document first to answer questions about its content.",
        description="Message explaining document is needed"
    )
    suggested_action: Optional[str] = Field(
        default="Load a document using the upload feature",
        description="Suggested user action"
    )
