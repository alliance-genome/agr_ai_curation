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

## Chat-Time Dispatch Boundary

Direct chat extraction must use the same ownership boundary as curation prep and
flow execution. The extractor should not call the validator agent directly, and
the supervisor should not decide how to validate extractor-owned proposals.
Instead, the specialist tool/runtime wrapper should intercept domain-envelope
extractor output before returning it to the supervisor:

1. Supervisor routes a paper gene question to `gene_extractor`.
2. `gene_extractor` emits a `GeneExtractionResultEnvelope` with
   `gene_mention_evidence` objects and proposal/context fields.
3. The runtime converts that extraction result into an in-memory
   `DomainEnvelope`.
4. The runtime calls `dispatch_active_validator_bindings(...)` for active
   domain-pack bindings.
5. The package-scoped `gene_validation` agent receives a
   `DomainValidationRequest` built from the binding selectors.
6. Validator `resolved_values`, lookup attempts, candidates, and findings are
   materialized back into the envelope.
7. The supervisor receives the validated/materialized envelope summary and then
   writes the chat answer.

This keeps validation deterministic and metadata-owned while still making
direct chat answers reflect the same active validator behavior users will see
in the workspace. Curation prep and workspace refresh continue to run their
persisted-envelope dispatch path; the chat bridge is a pre-supervisor
materialization of the same active bindings, not an extractor-side tool call.

## Extractor Output Contract

Extractor payloads should distinguish proposed identity from validated identity.

Recommended extractor-owned fields:

```yaml
mention: crumbs
species: Drosophila melanogaster
taxon_hint: NCBITaxon:7227
data_provider_hint: FB
proposed_primary_external_id: FB:FBgn0259685
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
primary_external_id: FB:FBgn0259685
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

## Cross-Agent Inventory

This inventory captures the repo state after the extractor tool-boundary cleanup
committed through `24b8c966` on 2026-05-19. "Active bindings" are the bindings currently under
`validator_bindings.active`; bindings listed under `under_development` are
important rollout candidates but should not be treated as dispatch evidence
until promoted.

| Extractor agent | Validator agent | Domain pack | Active bindings | Extractor tools allowed | Extractor tools removed | Validator tools required | Expected materialized fields |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gene_extractor` | `gene_validation` | `agr.alliance.gene` | `alliance_gene_reference_lookup` | `search_document`, `read_section`, `read_subsection`, `record_evidence`, `get_agent_contract`, `agr_species_context_lookup` | `agr_curation_query` gene lookup methods | `get_agent_contract`, `agr_curation_query` | `primary_external_id`, `gene_symbol`, `taxon` from `curie`, `symbol`, `taxon` |
| `allele_extractor` | `allele_validation` | `agr.alliance.allele` | `allele_mention_reference_validation` | `search_document`, `read_section`, `read_subsection`, `record_evidence`, `get_agent_contract`, `agr_species_context_lookup` | Broad `agr_curation_query` allele and reference lookup | `get_agent_contract`, `agr_curation_query` | `allele.primary_external_id`, `allele.allele_symbol`, `allele.taxon` |
| `chemical_extractor` | `chemical_validation`, `ontology_term_validation`, `controlled_vocabulary_validation` | `agr.alliance.chemical_condition` | `chemical_condition.chebi_api_lookup`, `chemical_condition.term_chebi_api_lookup`, `chemical_condition.condition_ontology_lookup`, `chemical_condition.condition_relation_type_lookup` | `search_document`, `read_section`, `read_subsection`, `record_evidence`, `get_agent_contract`, `agr_species_context_lookup` | Broad `agr_curation_query` chemical, ontology, and vocabulary lookup; direct ChEBI lookup is validator-owned | `chemical_validation`: `get_agent_contract`, `chebi_api_call`; `ontology_term_validation` and `controlled_vocabulary_validation`: `get_agent_contract`, `agr_curation_query` | `condition_chemical.curie`, `condition_chemical.name`, `curie`, `name`, `condition_class.curie`, `condition_relation_type.name`, `condition_relation_type.vocabulary`, `condition_relation_type.id` |
| `disease_extractor` | `ontology_term_validation`, `controlled_vocabulary_validation`, `data_provider_validation` | `agr.alliance.disease` | `disease_ontology_term_lookup`, `disease_relation_cv_lookup`, `disease_condition_relation_lookup`, `disease_data_provider_lookup` | `search_document`, `read_section`, `read_subsection`, `record_evidence`, `get_agent_contract`, `agr_species_context_lookup` | Broad `agr_curation_query` disease, CV, ontology, subject, and reference lookup | `get_agent_contract`, `agr_curation_query`; standalone `disease_validation` also uses package lookup but is not an active domain-pack binding | `disease_annotation_object.curie`, `disease_annotation_object.name`, `disease_relation_name`, `disease_relation_vocabulary`, `disease_relation_id`, `condition_relations[0].condition_relation_type.*`, `data_provider.abbreviation` |
| `gene_expression_extraction` | `controlled_vocabulary_validation`, `data_provider_validation` | `agr.alliance.gene_expression` | `relation_vocabulary_validation`, `data_provider_validation` | `search_document`, `read_section`, `read_subsection`, `record_evidence`, `get_agent_contract`, `agr_species_context_lookup` | Broad `agr_curation_query` relation, data-provider, anatomy, stage, assay, reference, and reagent lookup | `get_agent_contract`, `agr_curation_query` | `relation.name`, `relation.vocabulary`, `relation.id`, `data_provider.abbreviation` |
| `phenotype_extractor` | `ontology_term_validation` | `agr.alliance.phenotype` | `phenotype_term_ontology_validator` | `search_document`, `read_section`, `read_subsection`, `record_evidence`, `get_agent_contract`, `agr_species_context_lookup` | Broad `agr_curation_query` phenotype-term, subject, and reference lookup | `get_agent_contract`, `agr_curation_query` | `curie`, `label` for `PhenotypeTerm` payloads |
| None; shared validator invoked by phenotype/disease once promoted | `subject_entity_validation` with `gene_validation`, `allele_validation`, or `agm_validation` downstream | `agr.alliance.phenotype`, `agr.alliance.disease` | None active; `phenotype_subject_entity_validator` and `disease_subject_materialization` are `under_development` | Not extractor-owned | Subject lookup should stay out of extractors except proposed labels, type hints, identifiers, and taxon context | `subject_entity_validation`: `get_agent_contract`, `agr_curation_query`; downstream validators: `gene_validation`, `allele_validation`, `agm_validation` | Planned fields include `subject_identifier`, `subject_type`, `subject_label`, `taxon` |
| None; shared validator invoked by domain packs once promoted | `reference_validation` | `agr.alliance.allele`, `agr.alliance.chemical_condition`, `agr.alliance.gene_expression`, `agr.alliance.phenotype`, `agr.alliance.disease` | None active in these packs; source/reference bindings are `under_development` | Extractors may preserve paper-backed `pmid`, `doi`, title, citation, and document context | Literature API lookup should stay validator-owned | `get_agent_contract`, `agr_literature_reference_lookup` | Planned fields include `reference_id`, `curie`, `title` |
| None; shared validator invoked by chemical/disease once promoted | `experimental_condition_validation` | `agr.alliance.chemical_condition`, `agr.alliance.disease` | None active; `experimental_condition_validation` is `under_development` | Extractors may preserve condition text, relation hints, component mentions, quantities, and evidence | Composite condition normalization should stay validator-owned | `get_agent_contract`, `agr_curation_query`, `chebi_api_call` | Planned fields include `ExperimentalCondition.condition_id` and normalized component fields |
| None; validator-only helper | `agm_validation` | Used through subject-entity planning for phenotype and disease | None active directly in inspected packs | Not extractor-owned | AGM identifier or label lookup should stay validator-owned | `get_agent_contract`, `agr_curation_query` | Planned subject fields routed through `subject_entity_validation` |
| None; lookup helper agents | `gene_ontology_lookup`, `go_annotations_lookup`, `orthologs_lookup` | No active domain-pack validator binding found in the inspected Alliance packs | None | Not extractor-owned unless a future pack declares explicit context-only use | Lookup helper calls should not be used as hidden materializers | `quickgo_api_call`, `go_api_call`, or `alliance_api_call` by helper | None until a domain pack declares expected result fields |

Rollout implications:

- Gene is the reference implementation: the extractor has a narrow species
  context tool, the active binding feeds proposed/context fields, and validator
  lookup attempts are visible in chat streaming.
- Allele, chemical condition, disease, gene expression, and phenotype now use
  the same extractor-side narrow context boundary. Their prompts forbid
  extraction-time identity lookup and preserve paper-backed values only as
  selector hints for validators.
- Structural pending-envelope/data-check bindings for allele, chemical, and
  disease are under-development metadata, not active validator dispatch. They
  need concrete selector inputs and expected materialization targets before
  promotion.
- Disease validation now uses package-owned `agr_curation_query` helpers rather
  than direct `curation_db_sql`. Active disease domain-pack bindings still route
  through shared ontology, controlled-vocabulary, and data-provider validators,
  so disease is no longer a direct-SQL exception to the validator tool-boundary
  policy.
- Gene expression already has active relation and data-provider validators, so
  it is not a "no active validators" domain. Its gap is coverage: anatomy,
  stage, assay, reagent, gene, and reference identity checks are not active
  bindings in the inspected pack.
- Reference, subject-entity, experimental-condition, and AGM validators are
  shared validators or planned validator routes, not extractor domains. Their
  rollout should promote specific domain-pack bindings only after the extractor
  payload fields and expected materialization targets are explicit.

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
- Should `agr_species_context_lookup` stay as the shared narrow provider/taxon
  tool for all extractors, or should domain-specific context helpers be added
  where species context alone is insufficient?
- Should species-name-to-provider mapping use deterministic config only, or may
  it query the curation database when names are ambiguous?
