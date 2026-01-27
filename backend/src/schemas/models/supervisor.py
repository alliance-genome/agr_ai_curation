"""Supervisor envelope schema.

Used by supervisor agent for routing decisions and execution planning.
"""

from typing import Optional
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope, RoutingPlan


class SupervisorEnvelope(StructuredMessageEnvelope):
    """Envelope for supervisor routing decisions"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="supervisor", description="The supervisor agent")
    routing_plan: RoutingPlan = Field(description="Dynamic routing plan with execution order")
    needs_document_context: bool = Field(default=False, description="Whether PDF context is required")
    immediate_response: Optional[str] = Field(
        default=None,
        description="If execution_order is empty or first step is immediate_response, the actual response text"
    )
    query_type: Optional[str] = Field(
        default=None,
        description="Type of query: greeting, question, document_query, etc."
    )
