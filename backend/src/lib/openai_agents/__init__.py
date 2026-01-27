"""
OpenAI Agents SDK implementation for AI Curation Prototype.

This module provides a full agent-based architecture using OpenAI Agents SDK.

Architecture:
- Supervisor agent analyzes queries and routes to specialists
- Specialists can hand back to supervisor for multi-step queries
- Bidirectional handoffs enable complex cross-domain synthesis

Domain Specialists (created by supervisor):
- PDF Specialist: Document Q&A using Weaviate hybrid search
- Disease Ontology Specialist: DOID database queries via SQL
- Gene Curation Specialist: AGR database queries
- Chemical Ontology Specialist: ChEBI REST API queries

Tools:
- SQL query (for disease ontology)
- REST API (for ChEBI)
- AGR curation query (for gene data)
- Weaviate hybrid search (for PDF content)
"""

from .pdf_agent import create_pdf_agent
from .runner import run_agent_streamed
from .agents import create_supervisor_agent

__all__ = [
    # Main entry point
    "run_agent_streamed",
    # Agent factories
    "create_pdf_agent",
    "create_supervisor_agent",
]
