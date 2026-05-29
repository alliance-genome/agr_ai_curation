# Ontology Resolution Reference Research

Date: 2026-05-29

Companion note for
[`2026-05-29-gene-expression-linkml-extraction-failure-notes.md`](./2026-05-29-gene-expression-linkml-extraction-failure-notes.md).

This should stay standalone. The gene-expression document is the problem and
failure-mode narrative; this document is reusable research for paper-text to
controlled-vocabulary term resolution across anatomy, stage, assay/method, GO,
and future domain-pack fields.

## Local Reference Workspace

Reference material was collected under the gitignored local workspace:

```text
temp/ontology_resolution_refs/
```

The cloned repos in that workspace have local pre-commit secret-scanning hooks
installed. The workspace is research material only; the design conclusions below
should be treated as the tracked source of record.

## Local Inventory

### Repositories

- `temp/ontology_resolution_refs/repos/scispacy`
  - Source: https://github.com/allenai/scispacy.git
  - Useful areas:
    - `scispacy/candidate_generation.py`
    - `scispacy/linking.py`
    - `scispacy/linking_utils.py`
    - `scispacy/abbreviation.py`
  - Main idea: build a KB of canonical names plus aliases, generate candidates
    using character 3-gram TF-IDF over aliases, then threshold and filter
    candidates.

- `temp/ontology_resolution_refs/repos/ncbo_annotator`
  - Source: https://github.com/ncbo/ncbo_annotator.git
  - Useful areas:
    - `lib/ncbo_annotator.rb`
    - `lib/ncbo_annotator/mgrep/README.md`
  - Main idea: dictionary annotation over labels and synonyms, with controls
    for whole-word matching, longest-only span selection, stop words, minimum
    term length, semantic-type filtering, hierarchy expansion, and mapping
    expansion.

- `temp/ontology_resolution_refs/repos/ols4`
  - Source: https://github.com/EBISPOT/ols4.git
  - Useful areas:
    - `backend/src/main/java/uk/ac/ebi/spot/ols/repository/search/OlsSearchQuery.java`
    - `backend/src/main/java/uk/ac/ebi/spot/ols/repository/search/OlsSearchClient.java`
    - `backend/src/main/java/uk/ac/ebi/spot/ols/controller/api/v2/V2TextTaggerController.java`
    - `backend/src/main/java/uk/ac/ebi/spot/ols/service/TextTaggerService.java`
  - Main idea: PostgreSQL-backed term search with label, synonym, and
    definition fields; tsvector ranking; exact-label boosts; trigram similarity
    for suggest; facets/filters; and a text tagger for dictionary matching.

- `temp/ontology_resolution_refs/repos/PPRSSM`
  - Source: https://github.com/lasigeBioTM/PPRSSM.git
  - Useful areas:
    - `src/generate_candidates.py`
    - `go_src/ontology.py`
    - `ppr_for_ned*.java`
  - Main idea: generate candidate lists first, then use ontology graph
    structure and semantic similarity to rerank candidates by document-level
    coherence.

- `temp/ontology_resolution_refs/repos/ontology-access-kit`
  - Source: https://github.com/INCATools/ontology-access-kit.git
  - Useful areas:
    - `src/oaklib/datamodels/search.py`
    - `src/oaklib/interfaces/search_interface.py`
    - `src/oaklib/implementations/sqldb/sql_implementation.py`
    - `src/oaklib/utilities/lexical/lexical_indexer.py`
    - `src/oaklib/interfaces/text_annotator_interface.py`
  - Main idea: implementation-neutral ontology APIs for labels, aliases,
    mappings, graph traversal, search, lexical indexes, text annotation, and
    SSSOM export.

- `temp/ontology_resolution_refs/repos/uberon`
  - Source: https://github.com/obophenotype/uberon.git
  - Useful areas:
    - `uberon.obo`
    - `uberon-with-isa.obo`
    - bridge/reference files
  - Main idea: anatomy ontology source material and cross-species anatomy
    modeling patterns.

### Papers And Captures

- `temp/ontology_resolution_refs/papers/scispacy-w19-5034.pdf` and `.txt`
  - scispaCy paper.
  - Key local search anchors: `character 3-grams`, `aliases`, `abbreviation`.

- `temp/ontology_resolution_refs/papers/ppr-ssm-bmc.pdf`, `.txt`, and
  `ppr-ssm-pmc6819326.xml`
  - PPR-SSM paper.
  - Key local search anchors: `candidate list`, `semantic similarity`,
    `global coherence`, `Personalized PageRank`.

- `temp/ontology_resolution_refs/papers/uberon-bmc.pdf`, `.txt`, and
  `uberon-pmc3334586.xml`
  - Uberon paper.
  - Key local search anchors: `cross-species`, `anatomy`, `bridge`,
    `homology`, `mapping`.

- `temp/ontology_resolution_refs/papers/*recaptcha.html` and
  `temp/ontology_resolution_refs/papers/*download-page.html`
  - Failed direct PDF captures from PMC-style pages. Kept only as a note that
    the direct links were not usable in this environment.

- `temp/ontology_resolution_refs/papers/wormbase-pmc6424801-not-open-access.xml`
  - NCBI OA response indicating that PMCID was not available through the
    open-access fetch path.

## Search Design Takeaways

### Candidate Generation Should Be Multi-Channel

Lexical matching alone is too brittle for paper text to CV terms. We probably
want a candidate generator that combines:

- exact label and exact synonym;
- prefix and trigram similarity over labels/synonyms;
- definition, namespace, and context scoring;
- abbreviation expansion from the paper text;
- organism/domain-aware query expansion, such as ALM, PLM, mechanosensory
  neuron, neuron, axon, and soma for WB anatomy;
- optional vector search over labels, synonyms, and definitions as a recall
  layer, not as the authority.

The accepted term should still round-trip through the authoritative curation DB
and LinkML/domain-pack validation path.

### API Client PR Target

`temp/agr_curation_api_client/src/agr_curation_api/db_methods.py` still uses
exact -> prefix -> contains queries. The live curation DB already has `pg_trgm`
and trigram indexes on ontology term names and synonym names, so the API client
can likely expose a stronger scored search without a curation DB migration.

Desired client-side additions:

- query labels and synonyms with trigram similarity or word similarity;
- return score, matched field, matched string, and match mode;
- allow field weighting, such as label > synonym > definition;
- allow namespace and ontology-term-type filters;
- keep authoritative CURIE, name, definition, and synonyms in the result.

### Tool UX Pattern

The extraction agent should not be expected to magically know controlled terms
from paper prose. A better loop is:

1. Model proposes a field value using evidence text.
2. Resolver returns ranked candidates with explanations and missing/ambiguous
   field guidance.
3. Model selects a candidate or asks for a narrower search.
4. Tool validates the final CURIE/value against the authoritative schema.
5. Failure is explicit and actionable, not salvage/fallback.

### What Not To Copy Directly

- Do not make vector search or Weaviate the authority for ontology terms.
- Do not rely on raw dictionary annotation alone for final selection.
- Do not hide ambiguous matches. Ambiguity is useful signal and should be shown
  to the model/tool loop.
- Do not reintroduce extraction salvage. Resolver uncertainty should produce
  tool feedback or a validation failure.

## Implementation Surface From Repo Pass

The current code already has the right ownership boundary: Alliance-specific
curation and ontology helpers live in the Alliance package, while backend core
stays organization agnostic.

- Tool implementation: `packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py`
  already owns `_field_term_helper_policy()` and
  `get_domain_field_term_options()`. The new resolver tools should live here
  and share the domain-pack policy reader and AGR query wrapper.
- Tool registration: `packages/alliance/tools/bindings.yaml` declares the
  package tool surface. Add the three new static bindings here.
- Agent tool surface: `packages/alliance/agents/gene_expression/agent.yaml`
  currently exposes `get_domain_field_term_options`. Replace that single
  helper with the three resolver tools for gene expression once they land.
- Domain policy: `packages/alliance/domain_packs/gene_expression/domain_pack.yaml`
  already declares `term_helper` metadata for relation, assay, stage, anatomy,
  GO cellular component, and slim-term fields. Extend this metadata instead of
  hardcoding resolver behavior in prompts.
- Prompt owner: `packages/alliance/agents/gene_expression/prompt.yaml` is the
  right place for the extraction-time field-resolution workflow. The supervisor
  should route to the specialist, not carry field-specific ontology instructions.
- Output validation: `packages/alliance/python/src/agr_ai_curation_alliance/domain_packs/gene_expression/conversion.py`
  currently requires `metadata.provenance.helper_selections[]` from
  `get_domain_field_term_options` for `relation.name`. Replace the single
  helper name with the new accepted resolver provenance contract and expand it
  to the other controlled fields as the resolver becomes authoritative for
  extractor-side selections.
- Runtime summaries: `backend/src/lib/openai_agents/streaming_tools.py` already
  summarizes unresolved validation findings for the supervisor. Resolver
  instructions should be returned before final output; validator summaries should
  remain a fail-fast diagnostic rather than a hidden salvage loop.

An LSP references pass found no normal Python references to
`get_domain_field_term_options` because this tool is wired dynamically through
package YAML and prompts. That is expected and means the migration must update
the binding YAML, agent YAML, prompt YAML, domain-pack metadata, and conversion
validation together.

## Three-Tool Resolver Surface

We should expose three separate tools instead of one overloaded tool. The agent
needs different affordances when it is exploring, inspecting, and committing a
field value.

### 1. `search_domain_field_terms`

Purpose: broad, field-scoped candidate discovery from paper text.

Representative input:

```json
{
  "domain_pack_id": "agr.alliance.gene_expression",
  "object_type": "GeneExpressionAnnotation",
  "field_path": "expression_pattern.where_expressed.anatomical_structure",
  "query": "mechanosensory neurons",
  "evidence_context": {
    "quote": "RPM-1 was expressed in ALM and PLM mechanosensory neurons.",
    "page": 4,
    "section": "Results"
  },
  "data_provider": "WB",
  "taxon": "NCBITaxon:6239",
  "branch_root_curie": null,
  "limit": 10
}
```

Expected behavior:

- Load field policy from the domain pack.
- Generate candidates through multiple channels: exact label/synonym, trigram
  label/synonym, definition/context search, configured mappings, abbreviation
  expansion, and optional vector recall when available.
- Respect field, provider, taxon, ontology family, GO aspect, and branch
  constraints from the domain pack.
- Return candidates with `curie`, `name`, `ontology_type`, `matched_string`,
  `matched_field`, `match_mode`, `score`, `score_breakdown`, `path_hints`,
  `obsolete`, `lookup_attempts`, and `warnings`.
- Never mark a field as accepted. The tool should say which follow-up to take:
  call `resolve_domain_field_term` when one candidate is clearly intended, or
  call `inspect_ontology_term` when the candidate neighborhood needs review.

This is the tool we need because paper phrases rarely match CV labels exactly.
It gives the model a stronger search space without letting search become final
authority.

### 2. `inspect_ontology_term`

Purpose: bounded tree walking and term context inspection.

Representative input:

```json
{
  "domain_pack_id": "agr.alliance.gene_expression",
  "object_type": "GeneExpressionAnnotation",
  "field_path": "expression_pattern.where_expressed.anatomical_structure",
  "curie": "WBbt:0004017",
  "data_provider": "WB",
  "include_parents": true,
  "include_children": true,
  "include_siblings": true,
  "max_depth": 2,
  "limit": 25
}
```

Expected behavior:

- Return the authoritative term record, synonyms, definition, namespace,
  ontology type, obsolete/replacement status, and provenance.
- Return bounded parent/child/sibling/path context. Keep relation types explicit
  and cap depth/result count so the model cannot wander through an ontology.
- Preserve field-policy checks: if the inspected term is outside the allowed
  ontology family, provider, GO aspect, slim set, or branch, say so.
- Return action instructions such as "use this term", "search within this
  branch", "inspect the parent", or "do not use for this field".

This is the tree-walking tool. It should make navigation possible without
turning the model loose on arbitrary graph traversal.

### 3. `resolve_domain_field_term`

Purpose: final extractor-side field selection with provenance.

Representative input:

```json
{
  "domain_pack_id": "agr.alliance.gene_expression",
  "object_type": "GeneExpressionAnnotation",
  "field_path": "expression_experiment.expression_assay_used",
  "source_phrase": "CRISPR engineered GFP proteins",
  "evidence_context": {
    "quote": "Endogenous GFP knock-in strains were generated by CRISPR.",
    "page": 6,
    "section": "Methods"
  },
  "candidate_curie": "MMO:0000000",
  "data_provider": "WB",
  "taxon": "NCBITaxon:6239"
}
```

Expected behavior:

- Re-run or verify field-scoped search using the same policy as
  `search_domain_field_terms`.
- Accept only one authoritative candidate when it passes field policy and
  round-trips through the authoritative validation path available to the tool.
- Return `status: resolved`, `ambiguous`, `unresolved`, or `blocked`.
- For `resolved`, return the exact payload field instructions, selected
  candidate, and a `helper_selection` object the agent can copy into
  `metadata.provenance.helper_selections[]`.
- For `ambiguous`, return the competing candidates and the exact next tool call
  shape to disambiguate.
- For `unresolved`, return the literal paper phrase, failed lookup attempts, and
  the metadata field where the unresolved context should be preserved.
- For `blocked`, return the missing system capability, such as a missing API
  endpoint or provider context.

This is the only tool whose output should be allowed to justify setting a
controlled selector in the final extraction envelope.

## Prompting And Builder Loop

OpenAI's current GPT-5.5 prompting guidance emphasizes explicit workflows,
structured tool-use examples, persistence on multi-step tasks, preambles before
major tool work, and reflection after tool results. The official tool guide also
recommends shaping tool behavior through clear tool descriptions and
instructions, with orchestration handled by the agent/tool contract rather than
implicit model intuition.

For gene expression, the prompt should move from "call the helper somewhere" to
a small builder loop:

1. Search/read the paper and record verified evidence first.
2. Create an internal draft object ledger for each retained expression finding.
   The final response still must be JSON matching `GeneExpressionEnvelope`; the
   ledger is workflow state, not a new output format.
3. For each draft object, resolve required controlled fields before finalizing:
   `relation.name`, `expression_experiment.expression_assay_used`,
   `when_expressed_stage_name` or the explicit temporal context field, and each
   retained `expression_pattern.where_expressed` slot.
4. Use `search_domain_field_terms` when the evidence phrase is broad,
   organism-specific, abbreviated, or unlikely to match an ontology label.
5. Use `inspect_ontology_term` when candidates are close, hierarchical, or need
   parent/child/sibling context.
6. Use `resolve_domain_field_term` before writing any controlled selector into
   payload.
7. Copy the returned `helper_selection` into
   `metadata.provenance.helper_selections[]` and set payload values only from the
   resolver's accepted candidate.
8. If a required field cannot resolve, preserve the paper phrase in
   `metadata.ambiguities[]`, `metadata.normalization_notes[]`, or the domain-pack
   configured unresolved metadata path, then let validation fail explicitly. Do
   not invent terms and do not salvage.
9. Before final output, review the draft object against a field-resolution
   checklist and emit only valid JSON.

The tool outputs should include model-facing instructions, for example:

```json
{
  "status": "ambiguous",
  "field_path": "expression_pattern.where_expressed.anatomical_structure",
  "source_phrase": "mechanosensory neurons",
  "instructions": [
    "Do not set anatomical_structure yet.",
    "Search for evidence-local cell names ALM and PLM with data_provider WB.",
    "If ALM/PLM terms are selected, preserve mechanosensory neurons as the paper phrase in metadata.normalization_notes[]."
  ],
  "next_tool_call": {
    "tool": "search_domain_field_terms",
    "arguments": {
      "field_path": "expression_pattern.where_expressed.anatomical_structure",
      "query": "ALM neuron",
      "data_provider": "WB"
    }
  }
}
```

Prompt changes should also include a compact tool-use example for one anatomy
field and one assay field. The examples should show the model using search or
inspection first, then resolve, then copying provenance into the envelope. They
should not show chain-of-thought, patch DSLs, or manual quote generation.

## Domain-Pack Metadata Extensions

Add resolver metadata beside the existing `term_helper` declarations so the
tool can be generic across Alliance domain packs:

```yaml
term_helper:
  field_path: expression_pattern.where_expressed.anatomical_structure
  authority: selector_evidence
  resolver:
    primary_tool: resolve_domain_field_term
    search_tool: search_domain_field_terms
    inspect_tool: inspect_ontology_term
    accepted_provenance_tools:
      - resolve_domain_field_term
    query_expansion:
      evidence_local_terms: true
      abbreviation_expansion: true
      organism_specific_synonyms: true
    search_channels:
      - exact_label
      - exact_synonym
      - trigram_label
      - trigram_synonym
      - definition
      - configured_mapping
      - vector_recall
    unresolved_metadata_path: metadata.normalization_notes
```

This keeps organization-specific choices in package config. Backend core should
only load package metadata and execute package tools.

## Validation And Provenance Contract

The conversion layer should stop naming one legacy helper constant and instead
validate a resolver provenance shape:

```json
{
  "field_path": "expression_experiment.expression_assay_used",
  "source_tool": "resolve_domain_field_term",
  "source_phrase": "whole-mount in situ hybridization",
  "selected_value": "MMO:0000658",
  "selected_name": "whole mount in situ hybridization assay",
  "lookup_status": "success",
  "authority": "selector_evidence",
  "source": {
    "provider": "alliance_curation_db",
    "method": "scored_ontology_term_search"
  }
}
```

Acceptance rules:

- Required controlled fields must either have resolver provenance or explicit
  unresolved metadata plus a validation finding.
- `relation.name` should still require a live controlled-vocabulary value.
- Assay, stage, anatomy, and cellular-component selectors should require CURIE
  shape plus resolver provenance once the tools exist.
- Search and inspect outputs are supporting evidence only. Final payload fields
  need `resolve_domain_field_term` provenance.
- No compatibility fallback should let the model set controlled selectors from
  memory. Existing private helper code can be reused internally, but the agent
  surface should be the new three-tool workflow.

## Test Plan

- Add unit tests beside
  `backend/tests/unit/lib/openai_agents/tools/test_alliance_agr_curation_vocabulary_helpers.py`
  for all three tools with monkeypatched AGR query responses.
- Add contract tests for package binding discovery so the three tools appear in
  the Alliance package and not in backend core.
- Add prompt/tool-surface tests confirming the gene-expression agent exposes the
  three resolver tools and no longer exposes `get_domain_field_term_options`.
- Add conversion tests for accepted resolver provenance on relation, assay,
  stage, anatomy, and GO cellular-component fields.
- Add failure tests for ambiguous and unresolved fields to confirm they produce
  validation findings rather than salvage.
- Add fixture-driven cases for:
  - `mechanosensory neurons`
  - `ALM neuron`
  - `PLM neuron`
  - `L4 larval stage`
  - `CRISPR engineered GFP proteins`

## Implementation Order

1. Add the three package tool functions and bindings using the current
   domain-pack policy reader and AGR query wrapper.
2. Add richer scored search support in `agr_curation_api_client`; until that PR
   lands, the search tool can expose current exact/prefix/contains results plus
   clear `limited_search_backend` warnings, but the gene-expression prompt
   should still use the new tool names.
3. Extend gene-expression domain-pack `term_helper` metadata with resolver
   configuration.
4. Update the gene-expression agent tool list and prompt to the builder loop.
5. Update conversion validation to require the new resolver provenance shape.
6. Run targeted Docker unit tests, then a local extraction smoke on the known
   failing paper.

## Most Promising Next Experiment

Build a small local prototype query against the curation DB for the known bad
cases:

- `mechanosensory neurons`
- `ALM neuron`
- `PLM neuron`
- `L4`
- `L4 larval stage`
- `CRISPR engineered GFP proteins`

Compare candidate sets from:

1. current `agr_curation_api_client.search_ontology_terms`
2. direct `pg_trgm` similarity over `ontologyterm.name`
3. direct `pg_trgm` similarity over synonym names
4. definition search for assay/method terms
5. query expansion from evidence-local terms

The likely first PR is in `agr_curation_api_client`, adding scored fuzzy
ontology term search and match metadata. The AI Curation repo would then consume
that richer response in anatomy/stage/assay resolver tools.

## References

- OpenAI GPT-5.5 prompt engineering guide, coding and agentic workflow guidance:
  https://developers.openai.com/api/docs/guides/prompt-engineering#coding
- OpenAI tools guide:
  https://developers.openai.com/api/docs/guides/tools
