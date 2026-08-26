# Phase C semantic-coverage checklist: `go_annotations`

This checklist is the authoritative inventory source for the typed
existing-annotation contract in
`packages/alliance/agents/go_annotations/prompt.yaml`. The agent remains a lookup
agent that authors `GOAnnotationsResult`; it does not stage extraction output.

## Contract decisions

- **GOA-01 — role:** Fetch current existing GO annotations and retain source
  evidence and source-record provenance for curator comparison.
- **GOA-02 — one source:** `go_api_call` is the only annotation lookup. It accepts
  a validated gene CURIE and returns a provider-neutral typed result from the Gene
  Ontology Consortium API. The prompt never constructs URLs, parses source fields,
  or falls back to QuickGO.
- **GOA-03 — complete fields:** Preserve the returned gene-product ID,
  term/aspect, evidence code and ECO ID,
  reference, relation, With/From, qualifier, negation, provider, product type, and
  per-association provenance.
- **GOA-04 — identifiers:** Concrete supported examples cover FB, HGNC, MGI, RGD,
  SGD, WB, and ZFIN. Invalid and unsupported identifiers are distinct statuses.
- **GOA-05 — scope:** Term search/hierarchy, gene-by-term lookup, enrichment, and
  annotation mutation remain outside this specialist. No cross-agent transfer.
- **GOA-06 — typed adapter statuses:** Preserve the exact tool statuses `ok`,
  `not_found`, `invalid_input`, `unsupported_identifier`, and `upstream_error`.
- **GOA-07 — no confidence invention:** Do not group, rank, or filter annotations
  into manual/automatic confidence classes; that policy is not supported by the
  source contract.
- **GOA-08 — validator mapping:** Only tool `ok` maps to validator `resolved`;
  every bounded non-ok status maps to `unresolved`.
- **GOA-09 — result fields:** Keep all shared validator fields plus typed
  `gene_id`, `gene_symbol`, `annotations`, `source`, and `source_url` roots.
- **GOA-10 — lookup provenance:** Map typed statuses deterministically to one
  `lookup_attempts` outcome; non-ok results carry no resolved facts.
- **GOA-11 — finalization:** Submit the complete typed payload to
  `finalize_go_annotations_lookup` and repair rejected output before finishing.
- **GOA-12 — bounded errors:** Invalid input requests a correction; unsupported
  namespaces do not dispatch another source; not-found returns no annotations;
  upstream errors never reuse or fabricate values.

## Ordered workflow

The `.invariants.txt` guard enforces this exact order: read the CURIE, call the
typed adapter once, branch on its five statuses, then directly copy successful
typed results and record lookup provenance.

## Core and group behavior

`agent.yaml` keeps `group_rules_enabled: false`. The locked core owns the JSON
output mandate, while this editable prompt retains the tool workflow, typed status
mapping, source/provenance contract, and structured-finalization requirement.

## Source contract details

The adapter calls only
`GET https://api.geneontology.org/api/bioentity/gene/{gene_id}/function?rows=-1`.
It accepts a supported Alliance gene CURIE rather than a URL, validates the
namespace-specific identifier before dispatch, requests the complete collection
in one call, and does not fall back to another annotation source.

Every successful annotation retains the returned gene-product ID, GO term and
aspect, legacy evidence code and ECO identifier, evidence label, references,
relation, With/From values, qualifiers, negation, providers, product type, and
exact source URL and association ID. The requested CURIE remains the result-level
identity because returned subjects are not uniformly prefix-identical: for
example, an `FB:` query can return a `FlyBase:` subject and an `HGNC:` query can
return a `UniProtKB:` product.

QuickGO remains the separate GO term metadata and hierarchy source. It is not an
annotation fallback because a direct request for the recorded `RGD:620474`
contract example rejects that identifier as an invalid gene-product ID.
