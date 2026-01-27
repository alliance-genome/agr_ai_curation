"""
OpenAI Agents SDK agent module.

This module provides factory functions for creating specialized agents
for biological curation tasks.

Agent Architecture (as_tool pattern):
- Supervisor: Routes queries to specialist tools, synthesizes results
- Specialists: Domain-specific agents that run in isolation via as_tool()

Each specialist agent:
- Has a dedicated factory function (create_*_agent)
- Uses tool_choice to enforce tool execution before responding
- Runs in isolation with its own context window (prevents context explosion)
- Returns only the final output to the supervisor
"""

from .supervisor_agent import create_supervisor_agent
from .disease_agent import create_disease_agent
from .gene_agent import create_gene_agent
from .chemical_agent import create_chemical_agent
from .allele_agent import create_allele_agent
from .orthologs_agent import create_orthologs_agent
from .gene_expression_agent import create_gene_expression_agent
from .gene_ontology_agent import create_gene_ontology_agent
from .go_annotations_agent import create_go_annotations_agent
from .ontology_mapping_agent import create_ontology_mapping_agent

# File output formatters
from .csv_formatter_agent import create_csv_formatter_agent
from .tsv_formatter_agent import create_tsv_formatter_agent
from .json_formatter_agent import create_json_formatter_agent

__all__ = [
    # Main supervisor
    "create_supervisor_agent",
    # Domain specialists
    "create_disease_agent",
    "create_gene_agent",
    "create_chemical_agent",
    "create_allele_agent",
    "create_orthologs_agent",
    "create_gene_expression_agent",
    "create_gene_ontology_agent",
    "create_go_annotations_agent",
    "create_ontology_mapping_agent",
    # File output formatters
    "create_csv_formatter_agent",
    "create_tsv_formatter_agent",
    "create_json_formatter_agent",
]
