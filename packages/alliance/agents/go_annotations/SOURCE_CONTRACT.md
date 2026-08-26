# Existing GO annotation source contract

The GO annotations specialist uses one authoritative path: the Gene Ontology
Consortium API endpoint
`GET https://api.geneontology.org/api/bioentity/gene/{gene_id}/function`.
The package adapter accepts a gene CURIE, not a URL. It validates the namespace
and namespace-specific local identifier before dispatch, makes one request, and
does not fall back to another source.

The adapter returns exactly one of these statuses:

- `ok`: the source returned one or more typed annotations.
- `not_found`: the source returned HTTP 404 or an empty `associations` list.
- `invalid_input`: the value is not a syntactically valid supported gene CURIE.
- `unsupported_identifier`: the CURIE is valid but its namespace is not supported.
- `upstream_error`: transport, HTTP, JSON, or source-contract validation failed.

Every `ok` annotation retains the source gene-product ID, GO term and aspect, legacy evidence code and
ECO identifier, evidence label, references, relation, With/From values,
qualifiers, negation, providers, product type, and a provenance object containing
the exact source URL and source association ID. The RGD contract is recorded in
`backend/tests/fixtures/alliance/go_annotations/rgd_620474_go_api.json` from the
current `RGD:620474` response, including `GO_REF:0000121`, RGD provider identity,
protein product type, and With/From identifiers.

The specialist result requires every annotation comparison field to be present,
including nullable and empty values. Structured finalization rejects any
annotation collection that omits, adds, or changes a field from the typed tool
output.

QuickGO is not an alternate annotation path. A direct QuickGO annotation request
for the same current RGD CURIE rejects `RGD:620474` as an invalid Gene Product ID,
so there is no validated lossless RGD mapping that would justify switching or
falling back to QuickGO. QuickGO remains the separate GO term metadata/hierarchy
tool used by the GO term specialist.

The requested gene CURIE remains the result-level identity. The source's returned
association subject is retained separately as `gene_product_id`, because current
supported mappings are not uniformly prefix-identical: for example, an `FB:` query
returns a `FlyBase:` subject and an `HGNC:` query may return a `UniProtKB:` product.
