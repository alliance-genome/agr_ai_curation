"""Direct response envelope schema.

Used for direct responses without document context.
"""

from typing import Optional
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class DirectResponseEnvelope(StructuredMessageEnvelope):
    """Envelope for direct responses without document context"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="direct_responder", description="The direct response agent")
    response_text: str = Field(description="The complete response to the user")
    response_type: Optional[str] = Field(
        default=None,
        description="Type of response: explanation, greeting, factual, etc."
    )
