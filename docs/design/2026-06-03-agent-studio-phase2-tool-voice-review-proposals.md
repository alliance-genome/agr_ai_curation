# Agent Studio Phase 2 — Tool Documentation Curator-Voice Review Proposals

Date: 2026-06-03
Reviewer role: curator-voice editor (review + draft only — no `bindings.yaml` edits, no commits)
Audience: working biocurators (biologists) browsing the Agent Studio tool catalog, with no programming background.

## 0. Status — DRAFTS FOR REVIEW (display-only)

These are proposed rewrites of the **tool** documentation that Agent Studio shows to
curators. The current text lives in `packages/alliance/tools/bindings.yaml` — each tool
has a top-level `description:` plus a `metadata.documentation` block (a `summary` and,
where present, `parameters[].description`). Two tools (`agr_curation_query`,
`agr_literature_reference_lookup`) also carry `methods` and `agent_methods`.

**These edits are curator-display only.** They do not change what any agent or model does
at runtime — the model reads its own separate instructions (docstrings), not this catalog
text. So every rewrite here is optimized purely for curator readability, not behavior.

**Framing rule applied throughout.** Where a tool stages a draft that a specialist
confirms later, the "stage / record / propose" tools are described as a **positive
handoff**: the extractor writes down what it saw, and a **stronger specialist validator
authoritatively confirms the official identity against the Alliance database**. No tool is
described as "forbidden", "guardrailed", or "not allowed to" do anything.

---

## 1. Summary

| Group | Count | Action |
|---|---|---|
| Builder tools (stage / patch / discard / finalize / list_staged across 5 domains) | 25 tools | Rewrite `description` + `summary` via per-verb templates |
| Builder-tool parameters (no descriptions today) | ~95 parameters | Author plain-language descriptions via per-parameter templates |
| `agr_curation_query` (tool itself) | 1 | Rewrite `description` + `summary` |
| `agr_curation_query` methods | 28 methods | Rewrite each method `description` |
| `agr_curation_query` agent_methods | 11 entries | Rewrite each `description` (positive validator framing) |
| Other individual tools | 18 tools | Rewrite `description` + `summary` (+ params where jargon-y) |
| Output formatters (`save_csv_file`, `save_tsv_file`, `save_json_file`) | 3 tools | **No change** — already curator-friendly |

**Totals:** 44 tools touched (25 builder + `agr_curation_query` + 18 others), 28 query
methods + 11 agent_method descriptions rewritten, ~95 builder parameters authored, 3 tools
left as-is.

**Tools whose purpose I could NOT fully pin down from the source** (flagged for Chris — see
Section 6): the three external lookup tools `chebi_api_call`, `quickgo_api_call`,
`go_api_call`, and `alliance_api_call` were **not in the task scope list** but are present
in `bindings.yaml`. I have drafted light curator-voice rewrites for them in Section 3 for
completeness, but please confirm whether they should be in scope.

---

## 2. Builder tools (25 tools) — templated by verb

The five extraction domains each get the same five builder verbs. The text is identical
except for the domain noun and the name of the specialist validator that confirms it. Each
template uses `{NOUN}` for the domain noun and `{VALIDATOR}` for the confirming specialist.

### 2.0 Domain table (fill the placeholders)

| Tool family (object type) | `{NOUN}` | `{VALIDATOR}` (the stronger specialist that confirms) |
|---|---|---|
| `gene_mention_evidence` | gene | the Gene Validation specialist, which confirms the official gene against the Alliance database |
| `allele_observation` | allele | the Allele Validation specialist, which confirms the official allele/variant against the Alliance database |
| `disease_observation` | disease annotation | the disease specialists, which confirm the disease term, the affected gene/allele/model, evidence codes, and provider against the Alliance database |
| `phenotype_observation` | phenotype annotation | the phenotype specialists, which confirm the affected entity and the phenotype term against the Alliance database |
| `gene_expression_observation` | gene-expression observation | the gene-expression specialists, which confirm the gene and the controlled terms against the Alliance database |

A short reminder of what "stage / draft" means for a curator: as an agent reads the paper,
it writes down each finding as a **draft entry** in a private scratch list for that one run.
Nothing is final yet. A specialist reviews each draft afterward and confirms the official
identity. These tools are how the agent builds and tidies that draft list.

### 2.1 Template — `stage_*` (start a new draft entry)

> **description:** Add one new draft {NOUN} to the run's working list, backed by the exact
> wording the agent found in the paper.
>
> **summary:** Records a single {NOUN} the agent read in the paper, together with the
> supporting quotes it captured. This is a first-pass draft, not a final record: {VALIDATOR}
> reviews it afterward and settles the official identity. One call adds one draft entry.

**Notable per-domain differences (call-outs):**

- **`stage_gene_mention_evidence`** — This one only captures a gene *mention* plus the
  agent's hints about which gene it might be (proposed symbol, ID, species). It deliberately
  does not try to settle the official gene; that is exactly what the gene specialist confirms
  next. Summary tail: "The agent records what it saw and its best identity hints; the Gene
  Validation specialist confirms the official gene."
- **`stage_disease_observation`** — Richest of the five: one draft carries the disease
  mention and disease term, the affected subject (gene, allele, or model), evidence codes,
  the relation (such as "is model of"), the data provider, and any experimental conditions.
  The choice of subject decides whether the final record is a gene, allele, or model disease
  annotation.
- **`stage_gene_expression_observation`** — One draft carries the gene whose expression was
  observed, the reference it came from, a plain "where expressed" statement, and the
  controlled-term choices (such as the anatomy and developmental-stage terms) that a term
  specialist confirms.

### 2.2 Template — `patch_*` (fix a draft already on the list)

> **description:** Make small, allowed corrections to a draft {NOUN} that is already on the
> run's working list.
>
> **summary:** Updates specific fields on one draft {NOUN} the agent staged earlier — for
> example fixing a typo in a captured value or attaching better supporting quotes. Only the
> small set of fields meant to be editable can be changed; the rest of the draft stays as
> recorded.

(No meaningful per-domain differences in the patch text. The set of editable fields differs
by domain, but the curator-facing summary stays the same.)

### 2.3 Template — `discard_*` (drop a draft from the list)

> **description:** Drop one draft {NOUN} from the run's working list when it turned out to be
> wrong or unsupported.
>
> **summary:** Marks one draft {NOUN} as withdrawn so it will not appear in the final result.
> The entry is set aside rather than erased: it stays in the run's history for auditing, with
> the short reason the agent gave for dropping it.

### 2.4 Template — `list_staged_*` (review the current list)

> **description:** Show a short summary of every draft {NOUN} currently on the run's working
> list.
>
> **summary:** Returns a compact overview of the {NOUN} drafts staged so far in this run, so
> the agent can review the working set before finishing. Withdrawn drafts are left out unless
> they are explicitly included.

### 2.5 Template — `finalize_*` (hand the finished list to the specialists)

> **description:** Submit the run's draft {NOUN} list so the specialists can confirm each one.
>
> **summary:** Takes the {NOUN} drafts the agent settled on and hands them off for
> confirmation by {VALIDATOR}. The agent proposes the set; the specialists confirm the
> official identities and produce the records that curators review.

**Notable per-domain differences (call-outs):**

- **`finalize_allele_extraction`** — Each finished allele draft is handed off as a small
  bundle of linked records (the allele, the paper, and how they connect). These records are
  held back from being written or exported on their own until the allele specialist confirms
  the official allele/variant. (Avoid the internal "BLOCKED write/export" and "4-object
  envelope" phrasing in curator text.)
- **`finalize_disease_extraction`** — The handoff turns each draft into the right kind of
  disease record (gene, allele, or model) based on the affected subject the agent chose.

### 2.6 Builder-tool parameters — templated by shared parameter name

Every builder tool currently ships its parameters with no descriptions. Below is one
plain-language description per shared parameter name; reuse the same text wherever that
parameter appears. Domain-specific parameters follow.

**Shared across most builder tools:**

| Parameter | Proposed description |
|---|---|
| `pending_ref_id` | A short label the agent gives this draft so it can refer back to the same entry later in the run. |
| `candidate_id` | Identifies which draft entry on the working list this action applies to. |
| `candidate_ids` | The list of draft entries the agent wants to hand off for confirmation. |
| `reason` | A short note explaining why the draft is being dropped, kept in the run's history. |
| `updates` | The specific fields to correct on the draft, each paired with its new value. |
| `mention` | The exact wording for this entity as it appears in the paper. |
| `evidence_record_ids` | The supporting quotes (captured earlier from the PDF) that back up this draft. |
| `source_mentions` | The exact paper phrases the agent read this finding from. |
| `include_discarded` | Whether to also show drafts that were previously withdrawn. |
| `confidence` | How sure the agent is about this finding (high, medium, or low). |
| `data_provider` | The Alliance group this belongs to, such as FB (FlyBase), WB (WormBase), MGI (Mouse), ZFIN (Zebrafish), RGD (Rat), SGD (Yeast), or HGNC (Human). |
| `condition_relations` | Any experimental conditions the paper explicitly tied to this finding (for example a drug treatment or temperature), with the kind of relationship each had to the result. Left empty when the paper states no conditions. |
| `negated` | True when the paper explicitly says the finding did NOT occur. |

**Gene mention (`stage_gene_mention_evidence`):**

| Parameter | Proposed description |
|---|---|
| `identity_resolution_notes` | The agent's notes on how it figured out which gene this mention refers to. |
| `species` | The species named in the paper for this gene, if stated. |
| `taxon_hint` | The NCBI Taxonomy ID (a standard species ID, e.g. NCBITaxon:7227 for fruit fly) the agent suggests, if it could tell. |
| `data_provider_hint` | The Alliance group the agent thinks this gene belongs to, as a hint for the specialist. |
| `proposed_primary_external_id` | The gene ID the agent proposes, offered as a hint; the gene specialist confirms the official one. |
| `proposed_gene_symbol` | The gene symbol the agent proposes, offered as a hint. |
| `proposed_taxon` | The species ID the agent proposes for this gene, offered as a hint. |

**Allele (`stage_allele_observation`):**

| Parameter | Proposed description |
|---|---|
| `normalized_hint` | A cleaned-up version of the allele name the agent suggests, offered as a hint for the allele specialist. |
| `associated_gene` | The gene this allele belongs to, if the paper names it. |
| `taxon` | The species this allele belongs to, if stated. |
| `reference_title` | The title of the paper the allele was read from. |
| `reference_filename` | The file name of the uploaded paper the allele was read from. |

**Disease (`stage_disease_observation`):**

| Parameter | Proposed description |
|---|---|
| `disease_name` | The disease name as the agent read it from the paper. |
| `disease_curie` | The Disease Ontology ID (DOID, a standard disease term ID) the agent proposes, if it found one; the disease specialist confirms it. |
| `role` | How the disease appears in this paper: the main disease studied (primary), mentioned as background, used for comparison (comparative), the disease being modeled (model context), or unspecified. This is the agent's read of the paper context, not a stored relationship. |
| `subject_type` | What kind of thing has the disease association — a gene, an allele, or a whole modified organism (model). This choice decides what kind of record is produced. |
| `subject_identifier` | The ID of the gene, allele, or model the disease is associated with. |
| `subject_label` | The name of the gene, allele, or model the disease is associated with. |
| `disease_relation_name` | The official relationship between the subject (gene, allele, or model) and the disease, chosen from the Disease Relation vocabulary — for example "is model of" or "is implicated in". The specialist confirms this term. |
| `evidence_code_curies` | The Evidence and Conclusion Ontology codes (ECO, a standard set of "how was this shown" codes) the agent proposes for this association. |
| `genetic_sex_name` | The genetic sex stated for this finding, if any (a standard controlled value). |
| `disease_qualifier_names` | Standard qualifier terms that refine the disease association, if the paper states any. |
| `with_gene_identifiers` | IDs of additional genes the paper says were involved together with the subject. |

**Phenotype (`stage_phenotype_observation`):**

| Parameter | Proposed description |
|---|---|
| `phenotype_annotation_object` | The plain-language description of the phenotype as stated in the paper. |
| `subject_identifier` | The ID of the gene, allele, or model that shows this phenotype. |
| `subject_label` | The name of the gene, allele, or model that shows this phenotype. |
| `subject_type` | What kind of thing shows the phenotype — a gene, an allele, or a whole modified organism (model). |
| `subject_taxon` | The species ID of the affected entity, if stated. |
| `term_curie` | The phenotype term ID the agent proposes, if it found one; a term specialist confirms it. |
| `term_label` | The phenotype term name the agent proposes. |
| `term_taxon_id` | The species ID tied to the proposed phenotype term, if relevant. |

**Gene expression (`stage_gene_expression_observation`):**

| Parameter | Proposed description |
|---|---|
| `where_expressed_statement` | A plain sentence describing where (and when) the gene was seen to be expressed, in the agent's words from the paper. |
| `subject` | The gene whose expression was observed, with the exact phrase from the paper and the agent's proposed symbol/ID. |
| `reference` | The paper the observation came from, with the exact phrase and its reference ID. |
| `controlled_fields` | The standard-term choices the agent made for this observation (such as the anatomy and developmental-stage terms), each paired with the value it selected. A term specialist confirms these. |

---

## 3. Other individual tools — before -> after

### 3.1 `agr_literature_reference_lookup` (Literature)

- **description**
  - before: `ES-backed Alliance literature reference lookup and fuzzy title/citation search through agr-curation-api-client`
  - after: **Look up a paper in the Alliance literature collection by its ID, or find candidate papers by title or citation text.**
- **summary**
  - before: `Resolve literature references from the Alliance literature Elasticsearch index through the official agr-curation-api-client package. Returns lookup attempts, candidate references, ambiguity/no-match details, and safe curator-facing status messages.`
  - after: **Finds papers in the Alliance literature collection. Give it a known ID (such as a PubMed ID or DOI) to fetch the exact paper, or a title/citation to search for likely matches. It reports what it tried, the candidate papers it found, any cases where the match was unclear or nothing matched, and a plain status message.**
  - Why: Drops "ES", "Elasticsearch index", and the client package name; explains what the tool finds and what it returns in plain terms.
- **methods**
  - `get_literature_reference`
    - before: `Exact lookup by PMID, DOI, AGRKB reference CURIE, MOD reference ID, or title using AGRCurationAPIClient(data_source="db").get_literature_reference().`
    - after: **Fetch one exact paper by a known identifier — a PubMed ID (PMID), DOI, Alliance reference ID, member-group reference ID, or exact title.**
  - `search_literature_references`
    - before: `Fuzzy reference search by title, citation, AGRKB ID, PMID, DOI, or MOD reference ID using AGRCurationAPIClient(data_source="db").search_literature_references().`
    - after: **Search for likely-matching papers by title, citation text, or any known identifier when you do not have an exact match.**
  - Why: Removes the internal client/method names; spells out PMID and "member-group reference ID" in plain language.
- **parameters** (existing text is mostly fine; minor cleanup)
  - `method` after: **Choose whether to fetch one exact paper by ID/title, or search for likely matches.**
  - `identifier` after: **A known ID or exact title for the paper you want — a PubMed ID, DOI, Alliance reference ID, or member-group reference ID.**
  - `query` after: **A title, citation, or identifier to search for when you do not have an exact match.**
  - `exact_match` after: **When searching, require an exact match instead of likely matches.**
  - `limit` after: **The most candidate papers to return from a search.**

### 3.2 `get_agent_contract` (Runtime Metadata)

- **description**
  - before: `Read-only deterministic agent contract lookup for tools, output schemas, domain-envelope metadata, validator bindings, ontology constraints, and field-level details`
  - after: **Look up an agent's setup details — which tools it uses, the shape of its output, which specialists confirm its findings, and the rules on individual fields — without running anything.**
- **summary**
  - before: `Inspect compact, deterministic runtime contract metadata for generated system agents without reading live envelope state or executing validation.`
  - after: **Shows a read-only summary of how a configured agent is set up: its tools, its output structure, the specialists tied to it, the ontology rules on its fields, and field-by-field details. It only reports the setup; it does not run the agent or change anything.**
  - Why: Replaces "contract metadata", "envelope state", "validator bindings" with plain descriptions of what the curator would see.
- **parameters**
  - `agent_id` after: **Which agent's setup you want to look at.**
  - `topic` after: **Which part of the setup to show: its tools, its output shape, its data structure, the specialists tied to it, its ontology rules, or one specific field.**
  - `field_path` after: **The specific field to look at, when you want details on just one field.**
  - `detail_level` after: **Choose a short overview or the full details.**

### 3.3 Grounding tools

**`search_domain_field_terms` (Database)**

- **description**
  - before: `Search domain-pack-declared ontology and controlled-vocabulary candidates for one controlled field without accepting a final selector`
  - after: **Search for candidate standard terms (from an ontology or controlled list) that could fill one specific field, based on a phrase the agent read in the paper.**
- **summary**
  - before: `Broad field-scoped candidate discovery from evidence-backed paper phrases. Search results are candidate guidance only; final selectors must be resolved with resolve_domain_field_term.`
  - after: **Casts a wide net for possible standard-term matches for one field, starting from a phrase backed by evidence in the paper. The results are suggestions to compare, not a final choice — the agent settles on the official term separately with Resolve Domain Field Term.**
  - Why: Keeps the positive handoff (suggestions vs. the authoritative resolve step) without the word "selector".
- **parameters**
  - `domain_pack_id` after: **Which curation area this field belongs to (for example, gene expression).**
  - `object_type` after: **The kind of record this field is part of.**
  - `field_path` after: **Which field the agent is trying to fill (for example, "where expressed").**
  - `query` after: **The phrase from the paper to look up candidate terms for.**
  - `evidence_context` after: **Optional supporting context from the paper — the source phrase, quote, figure, provider, or species.**
  - `data_provider` after: **The Alliance group to scope a species-specific anatomy lookup to.**
  - `taxon` after: **The species context, kept for the record.**
  - `branch_root_curie` after: **An optional starting point in the ontology to limit the search to one branch.**
  - `limit` after: **The most candidate terms to return.**
  - `exact_match` after: **Require an exact name match instead of close matches, where supported.**

**`inspect_ontology_term` (Database)**

- **description**
  - before: `Inspect one authoritative ontology term and bounded parent, child, or sibling context for a domain-pack field`
  - after: **Look at one ontology term in detail, plus a limited view of its neighbors in the term tree (broader terms above it, narrower terms below it, or terms beside it).**
- **summary**
  - before: `Bounded ontology term context inspection. This tool helps tree-walk and compare candidates but does not justify final controlled selector values.`
  - after: **Shows one ontology term and a small, bounded set of nearby terms so the agent can walk the term tree and compare candidates. It is for exploring and comparing; the official term choice is settled separately with Resolve Domain Field Term.**
  - Why: Explains "tree-walk" and keeps the positive handoff.
- **parameters**
  - `domain_pack_id` after: **Which curation area this field belongs to.**
  - `object_type` after: **The kind of record this field is part of.**
  - `field_path` after: **The field whose rules apply to this term.**
  - `curie` after: **The ID of the ontology term to look at.**
  - `data_provider` after: **The Alliance group to scope species-specific anatomy context to.**
  - `include_parents` after: **Also show the broader terms above this one.**
  - `include_children` after: **Also show the narrower terms below this one.**
  - `include_siblings` after: **Also show neighboring terms at the same level.**
  - `max_depth` after: **How many levels up or down the term tree to walk.**
  - `limit` after: **The most neighboring terms to return for each relationship.**

**`resolve_domain_field_term` (Database)**

- **description**
  - before: `Resolve one final domain-pack controlled field selector and return copyable resolver provenance`
  - after: **Settle the official standard term for one field and return a confirmed value with its supporting trail, ready to use.**
- **summary**
  - before: `Final controlled-field resolver. This is the only ontology/CV resolver tool whose output may justify writing a controlled selector into extractor JSON.`
  - after: **Confirms the one official standard term for a field, after the agent has explored candidates with the search and inspect tools. Its result is the trusted choice the agent can record, with a clear trail of how the term was decided.**
  - Why: Frames this as the authoritative confirming step (the positive counterpart to "search is only suggestions") without "selector"/"JSON".
- **parameters**
  - `domain_pack_id` after: **Which curation area this field belongs to.**
  - `object_type` after: **The kind of record this field is part of.**
  - `field_path` after: **Which field is being settled.**
  - `source_phrase` after: **The phrase from the paper that this term is being matched to.**
  - `evidence_context` after: **Optional supporting context — quote, page, section, provider, or species.**
  - `candidate_curie` after: **The ID of the candidate term picked from the search or inspect results.**
  - `candidate_value` after: **A candidate value that is a name rather than an ID, picked from the search results (for example a controlled-list term name).**
  - `data_provider` after: **The Alliance group to scope the lookup to.**
  - `taxon` after: **The species context, kept for the record.**
  - `limit` after: **The most candidates to check while confirming.**

### 3.4 PDF / evidence tools (Document & PDF Extraction)

A short orientation for curators (not part of the YAML — context for this review): when an
agent works through an uploaded PDF, it first **finds** relevant passages, then **reads** the
exact text, then **records** the precise quotes it wants to rely on as evidence, and can
**tidy** that evidence list (review, attach to a finding, correct, or withdraw). The tools
below cover those steps. Throughout, "passage" replaces the internal word "chunk" and
"this run's evidence list" replaces "active-run workspace".

**`search_document`**

- **description**
  - before: `Discovery search over uploaded PDF chunks using hybrid, lexical, or hybrid-lexical-first retrieval.`
  - after: **Search the uploaded PDF for passages relevant to a query, using meaning-based matching, exact word matching, or a blend.**
- **summary**
  - before: `Finds relevant chunks in the uploaded PDF. Use returned chunk_id values with read_chunk for final evidence span selection. Use lexical-heavy modes for exact-match terms and controlled identifiers; keep auto/hybrid for broad conceptual searches.`
  - after: **Finds passages in the uploaded PDF that match a query. Meaning-based search is best for broad concepts, while exact word matching is best for specific symbols and identifiers. The matching passages can then be read in full to pick out exact supporting quotes.**
  - Why: "passages" for chunks; explains the search modes by purpose rather than naming "hybrid/lexical/retrieval".
- **parameters**
  - `query` after: **What to search the paper for.**
  - `limit` after: **The most matching passages to return (default 5).**
  - `section_keywords` after: **Limit the search to certain sections, such as Methods or Results.**
  - `search_mode` after: **How to match: by meaning (best for broad concepts), by exact words (best for specific symbols and IDs), or a blend. The default blends both.**

**`read_chunk`**

- **description**
  - before: `Read one PDF chunk and return selectable evidence_spans[].span_id values.`
  - after: **Read the full text of one passage from the PDF and get back the exact pieces of it that can be saved as evidence.**
- **summary**
  - before: `Retrieves full raw chunk text, neighboring chunk IDs, and selectable evidence span IDs for record_evidence.`
  - after: **Returns the complete text of one passage, pointers to the passages just before and after it, and the specific snippets within it that can be captured as supporting evidence.**
  - Why: "passage" for chunk; "snippets that can be captured" for "selectable span IDs".
- **parameters**
  - `chunk_id` after: **Which passage to read, taken from a search result or a section's list of passages.**

**`read_section`**

- **description**
  - before: `Survey the full text of a specific document section.`
  - after: **Read the full text of a named section of the paper.**
- **summary**
  - before: `Retrieves complete section text for discovery and context. Use source_chunks[].chunk_id with read_chunk for final evidence selection.`
  - after: **Returns the complete text of a section for getting oriented and reading in context. The individual passages it lists can then be read in full to pick out exact supporting quotes.**
  - Why: removes "source_chunks[].chunk_id" jargon.
- **parameters**
  - `section_name` after: **The section to read, such as Methods or Introduction.**

**`read_subsection`**

- **description**
  - before: `Survey the full text of a specific subsection within a section.`
  - after: **Read the full text of a named subsection inside a section of the paper.**
- **summary**
  - before: `Retrieves subsection content for discovery and context. Use read_chunk on relevant chunks for final evidence selection.`
  - after: **Returns the complete text of one subsection for getting oriented and reading in context. The relevant passages can then be read in full to pick out exact supporting quotes.**
- **parameters**
  - `section_name` after: **The section that contains the subsection.**
  - `subsection_name` after: **The subsection to read.**

**`record_evidence`**

- **description**
  - before: `Create verified PDF evidence from backend-generated read_chunk span_ids.`
  - after: **Save one piece of supporting evidence from the PDF, using snippets picked out of a passage that was read.**
- **summary**
  - before: `Resolves selected evidence_spans[].span_id values against exact chunk text and returns backend-copied verified_quote plus locator metadata. Use one call for one evidence unit; multiple selected spans are stored as conjoined source fragments.`
  - after: **Turns chosen snippets from a passage into a saved piece of evidence with the exact verified quote and where it came from in the paper. Each call saves one piece of evidence; if several snippets are chosen together, they are stored as one joined quote.**
  - Why: "snippets", "verified quote", "where it came from"; drops span/locator/conjoined-fragment jargon.
- **parameters**
  - `entity` after: **The thing this evidence is about (for example, the gene or allele name).**
  - `span_ids` after: **The snippets to save, picked from a passage that was read.**

**`list_recorded_evidence`**

- **description**
  - before: `Review queued active-run evidence records before final output.`
  - after: **Review the evidence saved so far in this run before finishing.**
- **summary**
  - before: `Returns queued evidence records from the active-run evidence workspace so the agent can confirm the final support set. Discarded records are excluded unless include_discarded is true.`
  - after: **Shows the pieces of evidence saved so far in this run, so the agent can confirm the full set of support before finishing. Withdrawn evidence is left out unless it is explicitly included.**
- **parameters**
  - `include_discarded` after: **Also show evidence that was previously withdrawn.**
  - `object_id` after: **Show only evidence attached to this particular finding.**
  - `pending_ref_id` after: **Show only evidence attached to this particular draft finding.**

**`get_recorded_evidence`**

- **description**
  - before: `Fetch one active-run evidence record by ID for detailed review.`
  - after: **Look at one saved piece of evidence from this run in full detail.**
- **summary**
  - before: `Returns full details for one evidence record in the active-run workspace for detailed review.`
  - after: **Returns the complete details of one saved piece of evidence from this run.**
- **parameters**
  - `evidence_record_id` after: **Which saved piece of evidence to look at.**

**`attach_evidence_to_object`**

- **description**
  - before: `Attach active-run evidence to the intended curatable object or pending ref.`
  - after: **Link a saved piece of evidence to the finding it supports.**
- **summary**
  - before: `Adds object, pending-ref, and optional field-path metadata to an active evidence record without changing source quote or provenance.`
  - after: **Connects a saved piece of evidence to a finding (or to a specific field of it), without changing the quote itself or where it came from.**
- **parameters**
  - `evidence_record_id` after: **Which saved piece of evidence to link.**
  - `object_id` after: **The finding to link the evidence to.**
  - `pending_ref_id` after: **The draft finding to link the evidence to.**
  - `field_path` after: **The specific field this evidence supports, if it applies to just one field.**

**`detach_evidence_from_object`**

- **description**
  - before: `Detach active-run evidence from a wrong curatable object or pending ref.`
  - after: **Remove a link between a saved piece of evidence and a finding it was wrongly tied to.**
- **summary**
  - before: `Removes matching object, pending-ref, or field-path attachment metadata from an active evidence record.`
  - after: **Disconnects a saved piece of evidence from a finding (or from a specific field) it was linked to. The evidence itself stays saved.**
- **parameters**
  - `evidence_record_id` after: **Which saved piece of evidence to unlink.**
  - `object_id` after: **The finding to unlink the evidence from.**
  - `pending_ref_id` after: **The draft finding to unlink the evidence from.**
  - `field_path` after: **The specific field link to remove, if it applied to just one field.**

**`discard_recorded_evidence`**

- **description**
  - before: `Discard wrong or weak active-run evidence without deleting audit history.`
  - after: **Withdraw a saved piece of evidence that turned out to be wrong or too weak, while keeping it in the run's history.**
- **summary**
  - before: `Marks evidence discarded without deleting it. Discarded records remain available for audit and are omitted from final output by default.`
  - after: **Sets one piece of evidence aside so it will not be used in the final result. It is not erased — it stays in the run's history for auditing and is left out of the output by default.**
- **parameters**
  - `evidence_record_id` after: **Which saved piece of evidence to withdraw.**
  - `reason` after: **A short note on why the evidence should not be kept.**

**`update_recorded_evidence_metadata`**

- **description**
  - before: `Update only editable active-run evidence metadata.`
  - after: **Update the editable notes on a saved piece of evidence.**
- **summary**
  - before: `Updates agent-owned metadata such as entity, field_path, and agent_note. Source quote and provenance fields are immutable.`
  - after: **Changes the editable details on a piece of evidence — what it is about, which field it supports, and the agent's note. The quote itself and where it came from cannot be changed.**
- **parameters**
  - `evidence_record_id` after: **Which saved piece of evidence to update.**
  - `entity` after: **An updated label for what this evidence is about.**
  - `field_path` after: **The field this evidence supports.**
  - `agent_note` after: **A note on how this evidence should be used.**

### 3.5 `agr_species_context_lookup` (Database)

- **description**
  - before: `Resolve Alliance species, data provider, and NCBI taxon context without gene or allele lookup`
  - after: **Work out the species, the Alliance group, and the standard species ID for a paper, without looking up any genes or alleles.**
- **summary**
  - before: `Narrow extractor-safe lookup for provider/species/taxon context. This tool does not search gene names, gene IDs, synonyms, or generic entity mappings.`
  - after: **A focused lookup that matches a species name to its Alliance group (such as FlyBase or WormBase) and its standard NCBI Taxonomy ID. It is just for species and group context; it does not look up genes, alleles, or other entities.**
  - Why: "standard species ID" glosses NCBI taxon; "extractor-safe" jargon removed (the positive scope statement remains).
- **parameters** (existing text is reasonable; light cleanup)
  - `species` after: **A species name from the paper, such as Drosophila melanogaster or C. elegans.**
  - `data_provider` after: **An Alliance group code, such as FB, WB, MGI, HGNC, ZFIN, RGD, or SGD.**
  - `provider_name` after: **The full display name of the group or organism.**
  - `taxon_id` after: **A standard NCBI Taxonomy species ID, such as NCBITaxon:7227 (fruit fly).**
  - `limit` after: **The most matching groups to return.**

### 3.6 `curation_db_sql` (Database)

- **description**
  - before: `Query the Alliance Curation Database for disease ontology information.`
  - after: **Run a direct query against the Alliance Curation Database to look up Disease Ontology terms and how they relate to one another.**
- **summary**
  - before: `Executes SQL queries against the Alliance Curation Database to look up Disease Ontology (DOID) terms and relationships.`
  - after: **Runs a direct database query to find Disease Ontology terms (DOID, the standard set of disease term IDs) and the relationships between them.**
  - Why: spells out DOID; keeps it accurate that this is a direct database query.
- **parameters**
  - `query` after: **The database query to run against the curation database.**

### 3.7 External lookup tools — OUT OF TASK SCOPE (drafts offered; please confirm — see Section 6)

These four were not in the task's scope list, but they are real entries in `bindings.yaml`
with developer-ish text. Light drafts below if Chris wants them included.

**`chebi_api_call`** — description after: **Look up a chemical compound in ChEBI (Chemical Entities of Biological Interest, a standard chemical database).** / summary after: **Looks up chemical compound IDs and related information in ChEBI, the standard chemical-compound database hosted at EBI.**

**`quickgo_api_call`** — description after: **Look up a Gene Ontology term in QuickGO.** / summary after: **Looks up Gene Ontology (GO) term details — names, definitions, and relationships — from the QuickGO service.**

**`go_api_call`** — description after: **Look up Gene Ontology annotations for genes.** / summary after: **Retrieves Gene Ontology (GO) annotations for genes, including the evidence behind each one, from the QuickGO service.**

**`alliance_api_call`** — description after: **Look up which genes are counterparts of each other across species (orthology).** / summary after: **Finds orthology relationships — genes that correspond to one another across different species — using Alliance of Genome Resources data.**

Parameters on all four are identical (`url`, `method`, `headers_json`, `body_json`). These
are plumbing fields; if these tools stay in scope I would suggest leaving them as-is or, at
most: `url` -> **The web address to look up.** (The rest are technical request settings.)

---

## 4. `agr_curation_query` + its 28 methods

### 4.1 The tool itself

- **description**
  - before: `Structured AGR curation database queries with lookup attempts, candidate matches, classifications, provider projections, ontology term and controlled vocabulary lookup/search helpers, and flow validation attachment reference lookups`
  - after: **Look things up in the Alliance Curation Database — genes, alleles, ontology terms, controlled-vocabulary terms, species, and member groups — through one tool with many specific lookup methods.**
- **summary**
  - before: `A unified package-owned tool for querying the Alliance Curation Database. Different agents use different methods of this tool based on their specialization.`
  - after: **A single tool for looking up Alliance curation data. It offers many specific methods — search for a gene, fetch an allele by ID, look up an ontology term, list member groups, and more. Different agents use the methods that fit their job.**
  - Why: explains "methods" in plain terms; drops "package-owned".
- **parameters** (the catalog-level params are mostly clear inputs; the main fixes):
  - `method` after: **Which lookup to run (for example, search for a gene or fetch an allele by ID). This decides what is looked up.**
  - `gene_id` after: **A gene ID for a direct lookup.** (was "Gene CURIE for direct lookup" — "ID" preferred over "CURIE")
  - `allele_id` after: **An allele ID for a direct lookup.**
  - `entity_curies` after: **A list of entity IDs to look up facts for.**
  - `term` after: **A search word, an ID, or a controlled-vocabulary term to look up.**
  - `terms` after: **A list of term IDs to look up in bulk.**
  - `curies` after: **A list of IDs to map to display names.**
  - `ontology_term_type` after: **Which kind of ontology term to limit the lookup to (for example, a GO term or a mouse phenotype term).**
  - (Remaining params — `gene_symbol`, `gene_symbols`, `allele_symbol`, `allele_symbols`, `entity_type`, `entity_names`, `data_provider`, `provider_name`, `limit`, `vocabulary`, `term_name`, `abbreviation`, `synonym`, `category` — read fine as-is.)

### 4.2 The 28 methods — before -> after (grouped by area)

Genes:
- `search_genes` — before: `Search for genes by symbol using LIKE matching.` -> after: **Find genes whose symbol contains the text you give.**
- `search_genes_bulk` — before: `Bulk gene symbol search in one tool call.` -> after: **Search for many gene symbols at once in a single call.**
- `get_gene_by_exact_symbol` — before: `Find a gene by its exact official symbol.` -> after: **Find the gene whose official symbol exactly matches.**
- `get_gene_by_id` — before: `Retrieve detailed gene information by CURIE.` -> after: **Get full details for one gene by its ID.**

Alleles:
- `search_alleles` — before: `Search for alleles by symbol using LIKE matching.` -> after: **Find alleles whose symbol contains the text you give.**
- `search_alleles_bulk` — before: `Bulk allele symbol search in one tool call.` -> after: **Search for many allele symbols at once in a single call.**
- `get_allele_by_exact_symbol` — before: `Find an allele by its exact official symbol.` -> after: **Find the allele whose official symbol exactly matches.**
- `get_allele_by_id` — before: `Retrieve detailed allele information by CURIE.` -> after: **Get full details for one allele by its ID.**

Ontology terms:
- `search_anatomy_terms` — before: `Search species-specific anatomy ontology terms.` -> after: **Search anatomy terms for a specific species.**
- `search_life_stage_terms` — before: `Search species-specific developmental stage ontology terms.` -> after: **Search developmental-stage (life-stage) terms for a specific species.**
- `search_go_terms` — before: `Search Gene Ontology terms by name or keyword.` -> after: **Search Gene Ontology (GO) terms by name or keyword.**
- `get_ontology_term` — before: `Exact ontology term lookup by CURIE with optional ontologytermtype filtering.` -> after: **Fetch one exact ontology term by its ID, optionally limited to a certain kind of term.**
- `get_ontology_terms` — before: `Bulk exact ontology term lookup by CURIE.` -> after: **Fetch several exact ontology terms by their IDs at once.**
- `search_ontology_terms` — before: `Search ontology terms by label within a required curation DB ontologytermtype.` -> after: **Search ontology terms by name within one chosen kind of term (for example, mouse phenotype terms).**
- `map_curies_to_names` — before: `Bulk map CURIEs to display names for a supported helper category.` -> after: **Turn a list of IDs into their human-readable names.**

Controlled vocabulary:
- `get_vocabulary_term` — before: `Exact controlled vocabulary term lookup by vocabulary and term name, abbreviation, or synonym. Optional subset (a vocabularytermset name/id, or a list of them) restricts candidates to that subset's members; omit subset for the full vocabulary.` -> after: **Fetch one exact term from a controlled list by its name, abbreviation, or synonym. You can narrow the search to a named part of the list, or leave that out to search the whole list.**
- `search_vocabulary_terms` — before: `Search controlled vocabulary terms by vocabulary name/label and term name, abbreviation, or synonym. Optional subset (a vocabularytermset name/id, or a list of them) restricts candidates to that subset's members; omit subset for the full vocabulary.` -> after: **Search a controlled list for terms by name, abbreviation, or synonym. You can narrow it to a named part of the list, or search the whole list.**

Species & member groups:
- `get_species` — before: `List all supported species/organisms.` -> after: **List every supported species.**
- `get_data_providers` — before: `List all Alliance group data providers with abbreviation, taxon, and display name when available.` -> after: **List every Alliance member group, with its short code, species ID, and full name where available.**
- `get_data_provider` — before: `Resolve one Alliance data provider by abbreviation, provider display name, or taxon ID and preserve provider/taxon mismatch candidates.` -> after: **Look up one Alliance member group by its short code, full name, or species ID, and flag any cases where the group and species do not line up.**

Generic entities:
- `map_entity_names_to_curies` — before: `Resolve generic Alliance biological entity names or labels to CURIEs within explicit taxon context.` -> after: **Turn entity names or labels into their Alliance IDs, for a stated species.**
- `map_entity_curies_to_info` — before: `Retrieve generic Alliance biological entity facts by CURIE.` -> after: **Get facts about Alliance entities from their IDs.**

(Methods are otherwise grouped/consistent; the `name` fields like "Search Genes" already
read fine and need no change.)

### 4.3 `agent_methods` descriptions (11 entries) — positive validator framing

These describe which agent uses which methods. They read reasonably already; the main
cleanup is plain wording and the positive "stronger specialist confirms" framing.

- `agm_validation` — after: **The Affected Genomic Model (AGM) specialist confirms which modified organism a finding refers to, using its ID, name, and species.**
- `subject_entity_validation` — after: **The Subject specialist confirms the affected entity — sending it to the gene, allele, or modified-organism path depending on what it is.**
- `gene` — after: **The Gene specialist confirms gene identities and pulls full gene details against the Alliance database.**
- `allele` — after: **The Allele specialist confirms allele and variant identities against the Alliance database.**
- `gene_expression` — after: **The Gene Expression agent confirms gene names it found while reading the PDF.**
- `gene_ontology` — after: **The Gene Ontology agent looks up GO terms.**
- `ontology_term_validation` — after: **The Ontology Term specialist confirms exact ontology IDs and resolves term names or synonyms, keeping note of any cases that stayed unclear.**
- `controlled_vocabulary_validation` — after: **The Controlled Vocabulary specialist confirms terms by their list name, term name, abbreviation, and synonym.**
- `data_provider_validation` — after: **The Member Group specialist confirms group codes and checks that the group and species line up.**
- `experimental_condition_validation` — after: **The Experimental Condition specialist brings together ontology, controlled-vocabulary, member-group, species, and chemical checks into one decision for each condition.**
- (The `prompt_description` under `agent_studio` reads: "Structured API for Alliance curation database lookups…" — suggest after: **Structured lookups in the Alliance Curation Database. Narrow results to a member group: MGI, FB, WB, ZFIN, RGD, SGD, HGNC.**)

---

## 5. Parameters authored for non-builder tools

All non-builder parameter rewrites are folded inline into Section 3 next to their tool (so a
reviewer sees each parameter in context). No additional standalone parameter authoring is
needed: the non-builder tools already shipped parameter descriptions; the work there was
de-jargoning, not authoring from scratch. The ~95 newly-authored descriptions are the
builder-tool parameters in Section 2.6.

---

## 6. Open questions / things to confirm with Chris

1. **External lookup tools out of scope.** `chebi_api_call`, `quickgo_api_call`,
   `go_api_call`, `alliance_api_call` were not in the task scope list but are real
   `bindings.yaml` entries with developer-ish text. Drafts are in Section 3.7 — confirm
   whether to include them and whether their plumbing parameters (`url`, `method`,
   `headers_json`, `body_json`) should be touched.

2. **Disease `role` vs. `disease_relation_name`.** Both exist as separate staged fields on
   `stage_disease_observation` and the source does not spell out the difference in
   curator-facing terms (both relate the subject to the disease). I drafted `role` as "How
   the disease relates to the finding" and `disease_relation_name` as "the standard
   relationship phrase, such as 'is model of'". Please confirm that split, or clarify what
   `role` captures that the relation name does not.

3. **No tool's purpose was a hard blocker.** Every other tool's intent was clear from the
   `bindings.yaml` text plus the source signatures and docstrings; the rewrites above are
   grounded in that combined reading.
