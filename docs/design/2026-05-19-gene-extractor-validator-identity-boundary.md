# Gene Extractor And Validator Identity Boundary

Date: 2026-05-19

## Purpose

This design note clarifies the boundary between gene extraction and gene
validation in the domain-envelope workflow.

The goal is not to prohibit extractors from using database-backed context.
The goal is to make ownership explicit:

- The extractor owns paper interpretation, evidence, species context, and
  auditable proposed identity hints.
- The validator owns database confirmation, conflict detection, and final
  materialized normalized identity fields.
- The runtime owns validator dispatch and materialization into the persisted
  domain envelope.

This keeps the system honest when a paper gives partial, ambiguous, historical,
or species-dependent gene references.

## Current Tension

The gene extractor prompt already says active validator bindings are the
authority for final normalized identity. However, the current extractor schema
requires fields that read as final database facts:

- `primary_external_id`
- `gene_symbol`
- `taxon`

The active gene domain-pack binding also uses those same fields as validator
inputs. That means the validator can confirm or reject them, but the extractor
must already fill them before the validator can run cleanly.

This creates an ownership mismatch:

- In prose, the validator owns normalized identity.
- In schema and binding inputs, the extractor is forced to emit normalized
  identity first.

## Desired Boundary

The extractor should be allowed to use the paper and narrow contextual lookup
tools to determine organism context. It should not be allowed to perform gene
name or gene identifier resolution as an authoritative step.

Extractor responsibilities:

- Identify central genes or gene-like mentions in the paper.
- Decide whether the mention is experimentally central or background-only.
- Preserve exact paper mention text.
- Preserve evidence quotes and evidence record IDs.
- Preserve species/taxon/provider context when the paper supports it.
- Emit proposed or hinted identity fields when available from paper context or
  allowed contextual lookup.
- Preserve uncertainty, ambiguity, and exclusion notes.

Validator responsibilities:

- Validate proposed gene IDs against the Alliance curation database.
- Validate proposed symbols against current database facts.
- Validate that the resolved gene's taxon matches the proposed or inferred
  organism context.
- Resolve from mention plus species context when the extractor has no explicit
  gene ID proposal.
- Report unresolved, ambiguous, or conflicting results without guessing.
- Emit final `resolved_values` for fields materialized into the domain envelope.

Runtime responsibilities:

- Build `DomainValidationRequest` values from explicit domain-pack selectors.
- Dispatch active validator bindings after extraction.
- Materialize validator `resolved_values` into final payload fields.
- Preserve lookup attempts, candidates, findings, and curator messages.

## Extractor Output Contract

Extractor payloads should distinguish proposed identity from validated identity.

Recommended extractor-owned fields:

```yaml
mention: crumbs
species: Drosophila melanogaster
taxon_hint: NCBITaxon:7227
data_provider_hint: FB
proposed_primary_external_id: FB:FBgn0000368
proposed_gene_symbol: crb
proposed_taxon: NCBITaxon:7227
identity_resolution_notes:
  - Paper uses Drosophila melanogaster context and the Crumbs/crumbs name.
confidence: high
evidence_record_id: evidence-...
verified_quote: ...
page: 3
section: Results and Discussion
chunk_id: ...
```

The proposed fields are not final database truth. They are auditable claims
from the extractor that the validator can confirm, correct, or reject.

Final validator-owned fields should remain canonical domain-pack fields:

```yaml
primary_external_id: FB:FBgn0000368
gene_symbol: crb
taxon: NCBITaxon:7227
```

These final fields should be materialized from validator results, not required
as extractor admissions criteria.

## Validator Input Contract

The active gene validator binding should accept both proposals and context:

```yaml
input_fields:
  mention:
    source: payload
    path: mention
  proposed_gene_id:
    source: payload
    path: proposed_primary_external_id
    required: false
  proposed_symbol:
    source: payload
    path: proposed_gene_symbol
    required: false
  proposed_taxon:
    source: payload
    path: proposed_taxon
    required: false
  taxon_hint:
    source: payload
    path: taxon_hint
    required: false
  data_provider_hint:
    source: payload
    path: data_provider_hint
    required: false
  species:
    source: payload
    path: species
    required: false
  evidence_quote:
    source: payload
    path: verified_quote
    required: false
```

The expected result fields should continue to target canonical payload fields:

```yaml
expected_result_fields:
  curie: primary_external_id
  symbol: gene_symbol
  taxon: taxon
```

This allows two validator modes through the same contract:

- Verify mode: the extractor proposes a gene ID or symbol, and the validator
  confirms database facts.
- Resolve mode: the extractor only has a mention plus species context, and the
  validator attempts a bounded lookup or reports ambiguity.

## Tool Scoping

The extractor should not receive the broad `agr_curation_query` tool if that
tool exposes gene lookup methods such as:

- `search_genes`
- `search_genes_bulk`
- `get_gene_by_exact_symbol`
- `get_gene_by_id`

Prompt instructions alone are not enough here. If the extractor has access to a
broad tool, it can call gene lookup methods and blur the extraction/validation
boundary.

Preferred implementation options:

1. Add a narrow taxon/provider context tool for extractors.

   Example tool names:

   - `agr_taxon_context_lookup`
   - `agr_data_provider_lookup`
   - `agr_species_context_lookup`

   Allowed behavior:

   - Map provider abbreviations to taxon IDs.
   - Map known organism names or species strings to provider/taxon metadata.
   - Return provider display names, taxon CURIEs, and species labels.

   Disallowed behavior:

   - Gene symbol search.
   - Gene ID lookup.
   - Synonym-based gene resolution.
   - Entity-name-to-gene-CURIE mapping.

2. Add runtime-enforced method allowlists for tool bindings.

   The Alliance tool binding metadata already has `agent_methods` entries, but
   that should not be treated as the only enforcement mechanism unless the
   runtime rejects disallowed method calls. If `agr_curation_query` remains
   available to extractors, the runtime should enforce an extractor-specific
   allowlist that includes only provider/taxon methods.

For gene extraction, the intended allowlist is provider/taxon context only:

```yaml
gene_extractor:
  allowed_methods:
    - get_data_provider
    - get_data_providers
```

If species-name-to-taxon lookup is needed and `get_data_provider` is too
provider-code oriented, add an explicit method for that purpose rather than
granting gene search.

## Prompt Update Scope

This cleanup is a contract change, so prompt updates are part of the
implementation. The prompts should not merely be patched with new field names;
they should encode the same ownership boundary as the schema and validator
binding.

Gene extractor prompt updates:

- Replace final identity language with proposed identity language.
- Instruct the extractor to emit `proposed_*` and `*_hint` fields only when
  supported by paper context or allowed species/provider lookup.
- Remove instructions that encourage gene symbol, synonym, or gene ID database
  lookup.
- Keep species interpretation in scope because the extractor has paper context.
- Make clear that `primary_external_id`, `gene_symbol`, and `taxon` are final
  validator-materialized fields, not required extractor admissions criteria.
- Update every few-shot example and output-contract checklist to use
  `proposed_primary_external_id`, `proposed_gene_symbol`, `proposed_taxon`,
  `taxon_hint`, and `data_provider_hint` where relevant.

Gene extractor group-rule updates:

- Update each group-specific rule file so the required object fields are the
  evidence fields plus proposed/context fields.
- Remove bullets requiring extractor output to include final
  `payload.primary_external_id`, `payload.gene_symbol`, or `payload.taxon`.
- Keep organism-specific curation cautions, such as Drosophila/human symbol
  ambiguity, but phrase them as extraction/species-context guidance rather than
  final database resolution guidance.

Gene validator prompt updates:

- Teach the validator to consume `DomainValidationRequest.selected_inputs`
  containing `mention`, `proposed_gene_id`, `proposed_symbol`,
  `proposed_taxon`, `taxon_hint`, `data_provider_hint`, `species`, and
  `evidence_quote`.
- Describe two modes explicitly:
  - Verify mode: confirm or reject proposed gene ID/symbol/taxon values.
  - Resolve mode: use mention plus species/taxon/provider context to resolve or
    report ambiguity.
- Require conflict reporting when the proposed identity does not match database
  facts.
- Require final values to be returned through `resolved_values` using the
  binding keys `curie`, `symbol`, and `taxon`.

Supervisor or routing prompt updates should be minimal for the gene-only first
pass. The supervisor can continue routing paper gene-extraction requests to the
gene extractor and direct database lookup questions to the gene validator. The
important change is that the extractor's own prompt and tool list prevent it
from doing broad gene database lookup during extraction.

## Example Flow

Paper text says the work focuses on Crumbs in Drosophila.

Extractor:

1. Reads the title, abstract, results, figures, and methods.
2. Records evidence quotes for Crumbs/crumbs being experimentally central.
3. Uses narrow species/provider lookup if needed to confirm that Drosophila
   melanogaster maps to `FB` and `NCBITaxon:7227`.
4. Emits a `gene_mention_evidence` object with `mention`, evidence fields,
   `species`, `taxon_hint`, `data_provider_hint`, and optional proposed gene
   identity fields if the paper explicitly supports them.

Validator:

1. Receives the target object plus proposals and hints.
2. If `proposed_primary_external_id` is present, calls gene ID lookup and
   confirms current symbol and taxon.
3. If only a mention is present, searches genes using the provided provider or
   taxon hint.
4. Returns `resolved_values` for `curie`, `symbol`, and `taxon`, or returns
   `unresolved` with candidates and a curator-facing explanation.

Materializer:

1. Writes confirmed values into `primary_external_id`, `gene_symbol`, and
   `taxon`.
2. Preserves validator lookup attempts and candidate details.
3. Adds validation findings for ambiguity, conflicts, or missing expected
   fields.

## Migration Steps

1. Update the gene extractor payload schema.

   - Add proposed/hint fields.
   - Make final normalized fields optional or remove them from extractor-owned
     required fields.
   - Keep evidence fields required.

2. Update the gene extractor prompt and examples.

   - Replace final identity language with proposed identity language.
   - Remove instructions that ask the extractor to search gene names.
   - Keep instructions for paper-backed species/taxon/provider context.

3. Restrict gene extractor tools.

   - Remove broad `agr_curation_query` from `gene_extractor`, or wrap it with a
     provider/taxon-only tool.
   - Add runtime enforcement if method allowlists remain inside a broad tool.

4. Update the gene domain-pack validator binding.

   - Feed `mention`, proposed fields, and species/taxon/provider hints into the
     validator.
   - Materialize validator outputs into canonical final fields.

5. Update validator prompt/schema expectations.

   - Support verify mode and resolve mode.
   - Return conflicts explicitly when a proposed gene ID's database symbol or
     taxon disagrees with extractor context.

6. Update tests and fixtures.

   - Add fixture where extractor proposes a correct gene ID and validator
     confirms it.
   - Add fixture where extractor proposes a gene ID with the wrong taxon and
     validator flags conflict.
   - Add fixture where extractor only provides mention plus taxon hint and
     validator resolves.
   - Add fixture where mention plus taxon remains ambiguous and validator
     returns unresolved candidates.
   - Add tool-scope test proving gene extractor cannot call gene lookup methods.

## Non-Goals

- This design does not make the extractor responsible for final gene identity.
- This design does not remove useful paper-backed species interpretation from
  the extractor.
- This design does not require the validator to reread the whole paper.
- This design does not create or mutate canonical Alliance `Gene` rows.
- This design does not define paper-gene association export behavior.

## Open Questions

- Should final canonical fields be absent from extractor payloads until
  materialization, or present as nullable fields with validation status?
- Should proposed fields remain in the final review payload after validation,
  or move to object metadata after materialization?
- Should the provider/taxon lookup be a new narrow tool or a runtime-enforced
  view of `agr_curation_query`?
- Should species-name-to-provider mapping use deterministic config only, or may
  it query the curation database when names are ambiguous?
