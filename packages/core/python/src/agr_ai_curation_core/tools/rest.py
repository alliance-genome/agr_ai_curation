"""Package-local REST tool exports for the AGR core package."""

from .rest_api import create_rest_api_tool

chebi_api_call = create_rest_api_tool(
    allowed_domains=["ebi.ac.uk", "www.ebi.ac.uk"],
    tool_name="chebi_api_call",
    tool_description="Query ChEBI chemical database API (ebi.ac.uk only)",
)

quickgo_api_call = create_rest_api_tool(
    allowed_domains=["ebi.ac.uk", "www.ebi.ac.uk"],
    tool_name="quickgo_api_call",
    tool_description=(
        "Query QuickGO Gene Ontology API for GO terms, hierarchy, and relationships "
        "(ebi.ac.uk only)"
    ),
)

go_api_call = create_rest_api_tool(
    allowed_domains=["geneontology.org", "api.geneontology.org"],
    tool_name="go_api_call",
    tool_description=(
        "Query Gene Ontology API for gene annotations with evidence codes "
        "(geneontology.org only)"
    ),
)

alliance_api_call = create_rest_api_tool(
    allowed_domains=["alliancegenome.org", "www.alliancegenome.org"],
    tool_name="alliance_api_call",
    tool_description=(
        "Query Alliance of Genome Resources API for orthology data "
        "(alliancegenome.org only)"
    ),
)

__all__ = [
    "alliance_api_call",
    "chebi_api_call",
    "go_api_call",
    "quickgo_api_call",
]
