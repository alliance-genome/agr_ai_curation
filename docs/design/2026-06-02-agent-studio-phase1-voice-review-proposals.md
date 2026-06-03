# Agent Studio Phase 1 — Curator-Voice Review Proposals

Date: 2026-06-02
Reviewer role: curator-voice editor (review + draft only — no docs.yaml edits, no commits)
Audience: working biocurators (biologists) using the Agent Studio UI, with no programming background.

## 0. Status — SHIPPED (outcome recorded after drafting)

These were drafts for review. The rewrites shipped in commit `c64ba126`, with two
changes from what is proposed below — both reflecting Chris's decision that the
prohibition/guardrail framing is legacy and should be removed outright:

1. **Fully prohibition-free, not hedged.** The shipped extractor docs drop the
   "validator-owned" / "does not perform" clauses entirely in favor of a positive
   handoff, and go *further* than these drafts by framing the validator as **the
   stronger specialist that authoritatively confirms** each proposal
   (see [[feedback_validator_vs_extractor_division]]). For example, the shipped
   Gene Extractor handoff reads: "…to the Gene Validation Agent, the stronger
   specialist that authoritatively confirms each one against the Alliance database.
   The extractor proposes; the validator confirms and settles the official gene ID."

2. **The guardrail test was relaxed, not satisfied.** Every "Deliberately keeps
   'validator-owned' / 'does not perform' to satisfy the test" annotation in
   Section 2, and the "decision needed" framing in Section 5 below, are
   **SUPERSEDED**. The test `test_extractor_documentation_keeps_validator_boundary_clear`
   was renamed to `test_extractor_documentation_references_validator_handoff`; its
   `forbidden_fragments` set and its `assert "does not perform" … "validator-owned"`
   line were deleted; it now asserts only that each extractor doc still mentions the
   `validator` handoff. No shipped doc depends on the old prohibition tokens.

The CURIE cross-cutting sweep in Section 3 was reviewed and **declined** (CURIE
always appears as a secondary detail next to a real example like `MGI:3689906`).
The before/after rationale below is otherwise an accurate record of the voice pass.

## 1. Summary

**Agents reviewed:** 26 documentation files (25 `docs.yaml` plus the 2 system-agent
entries `task_input` and `curation_prep` in `system_agent_docs.yaml`).

**Need changes:** 12 agents.
**Already fine (no changes proposed):** 14 agents.

### Main themes in what needs changing

1. **Internal mechanics used AS the explanation.** Several docs explain what the
   agent does by naming the plumbing: "package-owned AGR curation lookup helpers,"
   "the package lookup contract," "Elasticsearch-powered text search,"
   "LIKE matching," "`<sup>` format," "indexed in Weaviate," "isBestScore flag,"
   "`is_manual=True`," "selector inputs," "materialized by validators." A curator
   does not know or care about any of this; it should describe what the lookup
   *does for them*.

2. **Guardrail / prohibition framing for validators and extractors.** Per the
   standing framing preference, validators should read as the *stronger,
   specialized resolver* (the expert that authoritatively looks things up), and
   extractors should lead with the *positive* division of labor (the extractor
   reads the paper and proposes; the specialist validator confirms against the
   database). Today the Disease Validator reads as a wall of "outside the contract /
   not exposed" boundaries, and the 5 extractors lead with "validator-owned,"
   "the extractor does not search," "does not materialize," etc.

3. **Undefined or buried acronyms / opaque field names.** `CURIE`, `HGVS`,
   `InChI`, `SMILES`, `DIOPT`, and `NCBITaxon` appear without a plain gloss, and a
   few example results show raw field names (`matched_id`, `taxon_match`,
   `is_negative=true`, `ambiguities[]`, `go_id`) as if the curator would read them.

### A note on the 5 extractors and the guardrail test

> **SUPERSEDED — see Section 0.** The drafts below were written to keep the old
> prohibition tokens so the guardrail test would still pass. Chris's decision was
> that the prohibition framing is legacy. As shipped, the 5 extractor docs
> (allele_extractor, disease_extractor, gene_expression_extraction, gene_extractor,
> phenotype_extractor) are fully prohibition-free, and the test was relaxed to
> assert only that each doc still mentions the `validator` handoff. The "keeps the
> required tokens" annotations in Section 2 no longer reflect the shipped text.

---

## 2. Agents that need changes

### Supervisor (`packages/core/agents/supervisor/docs.yaml`)

**Field:** `capabilities[0].example_result`
**Current:** `Routes to Gene Validation Agent`
**Proposed:** `Hands the question to the Gene Validation Agent`
**Why:** "Routes to" is pipeline-speak; "hands the question to" reads naturally and means the same thing.

**Field:** `capabilities[1].example_result`
**Current:** `Routes to PDF Specialist first, then Gene Validation Agent`
**Proposed:** `Sends the paper to the PDF Specialist first, then passes the genes it finds to the Gene Validation Agent`
**Why:** Same "routes" jargon; the rewrite also makes the two-step handoff concrete for a curator.

(The summary and limitations here are already plain and fine.)

---

### Gene Ontology Lookup (`packages/alliance/agents/gene_ontology/docs.yaml`)

**Field:** `summary`
**Current:** `Queries the Gene Ontology (GO) database via the QuickGO REST API to retrieve GO term information, hierarchy, and relationships across the three aspects: Molecular Function (MF), Biological Process (BP), and Cellular Component (CC).`
**Proposed:** `Looks up Gene Ontology (GO) terms — their definitions, broader and narrower terms, and how they relate — across the three GO categories: molecular function, biological process, and cellular component.`
**Why:** Removes "REST API" mechanics; "hierarchy and relationships" becomes "broader and narrower terms, and how they relate"; "aspects" softened to "categories."

**Field:** `capabilities[2].description`
**Current:** `Get direct child terms (more specific) of a GO term with relationship types (is_a, part_of, regulates).`
**Proposed:** `Shows the more specific terms directly underneath a GO term, and how each one relates to it (for example "is a kind of," "is part of," or "regulates").`
**Why:** `is_a`, `part_of`, `regulates` are raw identifiers used as the explanation; the plain glosses keep the meaning.

**Field:** `limitations[3]`
**Current:** `All responses must come from live QuickGO API queries - does not use cached/trained knowledge`
**Proposed:** `Every answer comes from a fresh, live lookup against the Gene Ontology — it never answers from memory.`
**Why:** "cached/trained knowledge" and "API queries" are developer terms.

**Field:** `limitations[4]`
**Current:** `API requests are restricted to ebi.ac.uk domain only`
**Proposed:** *(remove, or fold into the live-lookup note above)*
**Why:** This is an internal network-allowlist detail with no meaning for a curator. If retained at all, it should not appear as a curator-facing limitation.

(The data_source `species_supported` note "All species (GO is species-independent ontology)" is fine.)

---

### GO Annotations Lookup (`packages/alliance/agents/go_annotations/docs.yaml`)

**Field:** `capabilities[1].example_result`
**Current:** `Returns annotations filtered by is_manual=True with counts of manual vs automatic annotations`
**Proposed:** `Returns only the manually curated annotations, plus a count of how many are manual versus automatic`
**Why:** `is_manual=True` is a raw field expression used as the explanation.

**Field:** `capabilities[3].example_result`
**Current:** `Returns annotations filtered by aspect with go_id, go_name, and evidence`
**Proposed:** `Returns the matching annotations grouped by GO category, each with its GO ID, term name, and evidence`
**Why:** `go_id`/`go_name` are raw field names; "filtered by aspect" reads as plumbing.

(The summary, evidence-code capability, and the "use the GO Lookup Agent instead" limitations are clear and useful — leave them.)

---

### Disease Validation (`packages/alliance/agents/disease/docs.yaml`)

This doc is the strongest example of validator-as-prohibition framing plus internal
mechanics ("package-owned lookup helpers," "the package lookup contract,"
"direct SQL"). Reframe it as the specialist disease resolver.

**Field:** `summary`
**Current:** `Maps disease terms to Disease Ontology (DOID) identifiers through package-owned AGR curation lookup helpers.`
**Proposed:** `Confirms disease names against the Disease Ontology (DOID) and gives you the matching official disease term and its ID, so a disease annotation can be submitted with confidence.`
**Why:** "package-owned AGR curation lookup helpers" is pure internal mechanics used as the explanation; the rewrite says what it does and why the curator cares.

**Field:** `capabilities[3]` (whole item — `name`, `description`, `example_query`, `example_result`)
**Current:**
- name: `Hierarchy boundary`
- description: `Reports that direct hierarchy traversal is outside the package lookup contract for this validator path`
- example_query: `What are the parent terms of Alzheimer's disease?`
- example_result: `Returns an unresolved, curator-facing explanation instead of using direct SQL`
**Proposed:** *Remove this capability.* A "capability" describing what the agent *cannot* do, explained with "package lookup contract" and "direct SQL," does not belong in the capabilities list. The real limit is already covered in `limitations` (see next item). If a note is wanted, keep it only as a limitation.
**Why:** It is a prohibition dressed up as a capability, and it is explained entirely with internal mechanics.

**Field:** `data_sources[0]`
**Current:**
- name: `AGR curation lookup helpers`
- description: `Structured package helper methods for exact DOID lookup and typed Disease Ontology label or synonym search`
**Proposed:**
- name: `Alliance disease term records`
- description: `The Alliance's curated copy of the Disease Ontology — official disease names, their IDs, definitions, and synonyms.`
**Why:** "Structured package helper methods" and "typed … search" describe code, not a data source a curator would recognize.

**Field:** `limitations[3]`
**Current:** `Direct hierarchy traversal and obsolete-history analysis are not exposed by the package lookup contract`
**Proposed:** `Does not walk up or down the disease hierarchy (parent or child terms) and does not track a term's obsolete history — ask the Ontology Term Resolver for related terms.`
**Why:** "not exposed by the package lookup contract" is internal mechanics; the rewrite states the actual boundary plainly and points the curator somewhere useful.

**Field:** `limitations[4]`
**Current:** `All responses must come from package lookup evidence - cannot answer from general knowledge`
**Proposed:** `Every answer comes from a live lookup against the Alliance's disease terms — it never answers from memory.`
**Why:** "package lookup evidence" is developer phrasing.

---

### Chemical Validation (`packages/alliance/agents/chemical/docs.yaml`)

**Field:** `summary`
**Current:** `Maps chemical compound names to ChEBI (Chemical Entities of Biological Interest) ontology identifiers via the ChEBI REST API at EBI.`
**Proposed:** `Confirms chemical compound names against ChEBI (Chemical Entities of Biological Interest) and gives you the matching official ChEBI term and ID for a chemical annotation.`
**Why:** "via the ChEBI REST API at EBI" is mechanics; reframed as what it confirms for the curator. (ChEBI is already spelled out — good.)

**Field:** `capabilities[0].description`
**Current:** `Search for chemical compounds by name and return their ChEBI identifiers. Uses Elasticsearch-powered text search that supports partial matching and synonyms.`
**Proposed:** `Search for a chemical by name and get its ChEBI ID. Handles partial names and synonyms, so you don't need the exact official spelling.`
**Why:** "Elasticsearch-powered text search" is an implementation detail; the curator only needs to know partial names and synonyms work.

**Field:** `capabilities[1].description`
**Current:** `Get detailed information about a compound including definition, molecular formula, InChI, SMILES structure, and synonyms.`
**Proposed:** `Get the full details for a compound: its definition, molecular formula, standard chemical-structure codes (InChI and SMILES), and synonyms.`
**Why:** InChI and SMILES are undefined acronyms; a brief gloss ("standard chemical-structure codes") lets a non-chemist follow along while keeping the terms.

**Field:** `capabilities[2].description`
**Current:** `Retrieve parent classifications (is_a relationships) and child terms for a chemical compound in the ChEBI ontology hierarchy.`
**Proposed:** `Show the broader categories a compound belongs to, and the more specific compounds underneath it, within ChEBI.`
**Why:** `is_a relationships` is a raw identifier used as the explanation.

**Field:** `limitations[4]`
**Current:** `Requires API call before every response - never answers from training data alone`
**Proposed:** `Every answer comes from a fresh, live lookup against ChEBI — it never answers from memory.`
**Why:** "API call" and "training data" are developer terms.

(`capabilities[3]` batch lookup and the "cannot provide …" limitations are clear — leave them.)

---

### Allele Validation (`packages/alliance/agents/allele/docs.yaml`)

**Field:** `capabilities[0].description`
**Current:** `Search for alleles by symbol using LIKE matching - supports partial matches, synonyms, and case-insensitive search`
**Proposed:** `Search for alleles by symbol — partial symbols, synonyms, and any capitalization all work.`
**Why:** "LIKE matching" is a database operator; the curator only needs to know partial/synonym/case-insensitive search works.

**Field:** `capabilities[1].description`
**Current:** `Find an allele by its exact official symbol, with automatic conversion from paper notation (Gene<allele>) to database format (Gene<sup>allele</sup>)`
**Proposed:** `Find an allele by its exact official symbol. You can paste it the way it appears in a paper (for example Ulk1<tm1Thsn>) and it is matched automatically to the official superscript form.`
**Why:** `Gene<sup>allele</sup>` and "database format" expose internal text encoding; the rewrite keeps the helpful "paste it as written" point without the `<sup>` markup as the explanation.

**Field:** `limitations[2]`
**Current:** `Angle bracket notation in symbols must be converted to <sup> format for exact matches (handled automatically)`
**Proposed:** `Allele symbols written with angle brackets are matched to their superscript form automatically — you don't need to reformat them.`
**Why:** `<sup>` format is internal encoding; "handled automatically" is the part the curator cares about.

**Field:** `limitations[3]`
**Current:** `Very large result sets are capped at 500 results (default 100)`
**Proposed:** `Very broad searches return at most 500 matches (100 by default), so narrow your search if you don't see the allele you expect.`
**Why:** Minor — adds the "why it matters / what to do" the curator needs; current text is borderline but terse and jargon-light. Optional change.

(The `CURIE` mentions appear as a secondary detail in examples like "MGI:3689906," which is acceptable, but see the cross-cutting CURIE note in Section 3.)

---

### Orthologs Lookup (`packages/alliance/agents/orthologs/docs.yaml`)

**Field:** `summary`
**Current:** `Retrieves ortholog relationships across species using the Alliance of Genome Resources API with DIOPT-based confidence scoring.`
**Proposed:** `Finds a gene's orthologs (the matching genes in other species) and tells you how confident the prediction is, based on how many ortholog-prediction methods agree (the DIOPT approach).`
**Why:** "API" is mechanics, and `DIOPT` is an undefined acronym; the rewrite explains both ortholog and the confidence idea, while keeping DIOPT as a named-but-glossed detail.

**Field:** `capabilities[2].description`
**Current:** `Find the best-scoring ortholog in each species using the isBestScore flag`
**Proposed:** `Find the single best-scoring ortholog in each species.`
**Why:** `isBestScore flag` is a raw field name used as the explanation.

**Field:** `capabilities[2].example_result`
**Current:** `INSR (HGNC:6091) - isBestScore: Yes`
**Proposed:** `INSR (HGNC:6091) — flagged as the best match for human`
**Why:** Same raw field name leaking into the example.

**Field:** `limitations[1]`
**Current:** `Cannot search by gene symbol alone - requires resolved gene ID from Gene Validation Agent first`
**Proposed:** `Needs a confirmed gene ID, not just a symbol — run the gene through the Gene Validation Agent first.`
**Why:** Minor plain-language tidy; "resolved gene ID" reads slightly developer-ish. Meaning preserved.

(The "Alliance format with prefix" limitation and the paralog note are clear and genuinely useful — leave them.)

---

### Gene Extractor (`packages/alliance/agents/gene_extractor/docs.yaml`) — EXTRACTOR (test-constrained)

**Field:** `summary`
**Current:** `Extracts experimentally supported gene mentions from uploaded PDFs with evidence-first filtering, disambiguation, and validator-ready identity hints.`
**Proposed:** `Reads an uploaded paper and proposes the genes that have real experimental support in it, with the quote that backs each one — ready for the Gene Validation Agent to confirm against the Alliance database.`
**Why:** Leads with the positive division of labor (reads + proposes, validator confirms) instead of "validator-ready identity hints," which is jargon. Keeps the word "validator."

**Field:** `capabilities[3]` (`name`, `description`, `example_result`)
**Current:**
- name: `Validator-ready identity hints`
- description: `Preserves paper-backed mention text, species/taxon/provider context, and optional proposed identifiers for active gene validator bindings; the extractor does not search gene symbols or materialize final IDs.`
- example_result: `Returns retained gene mention envelopes with proposed/hint fields and verified evidence for downstream validator materialization`
**Proposed:**
- name: `Hands genes to the validator`
- description: `Passes each proposed gene — with its exact wording from the paper, the species, and the supporting quote — to the Gene Validation Agent, which does the authoritative database lookup. Settling on the final official gene ID is validator-owned; the extractor proposes, the validator confirms.`
- example_result: `Returns each proposed gene with its supporting quote, ready for the Gene Validation Agent to confirm`
**Why:** Positive handoff framing. **Deliberately keeps "validator" and "validator-owned"** so the guardrail test still passes, and avoids the forbidden phrases.

**Field:** `data_sources[1]`
**Current:**
- name: `Gene validator binding`
- description: `The active gene validator resolves or rejects proposed identity hints with Alliance lookup tooling after extraction.`
**Proposed:**
- name: `Gene Validation Agent`
- description: `The specialist that takes the proposed genes and confirms or rejects each one against the Alliance database after extraction.`
**Why:** "validator binding" and "lookup tooling" are mechanics; this names the partner agent plainly. Keeps "validator."

**Field:** `limitations[2]`
**Current:** `Multi-species disambiguation relies on context clues; ambiguous cases go to ambiguities[]`
**Proposed:** `When a gene could belong to more than one species, it uses clues in the text; cases it can't settle are flagged for curator review.`
**Why:** `ambiguities[]` is a raw field name.

**Field:** `limitations[3]`
**Current:** `Does not perform gene identity lookup itself; unresolved or conflicting identity remains visible as validator findings`
**Proposed:** `Does not perform the gene database lookup itself — that is the Gene Validation Agent's job; any unconfirmed or conflicting genes stay visible to you as validator findings.`
**Why:** Reframes the prohibition as a clear handoff. **Keeps "does not perform" and "validator"** for the guardrail test.

---

### Allele Extractor (`packages/alliance/agents/allele_extractor/docs.yaml`) — EXTRACTOR (test-constrained)

**Field:** `summary`
**Current:** `Extracts experimentally supported allele and variant mentions from uploaded PDFs, distinguishing alleles from strains, transgenes, and balancers.`
**Proposed:** *(leave as-is — already plain and clear)*
**Why:** No change needed to the summary.

**Field:** `capabilities[0].description`
**Current:** `Scans the paper for allele symbols, variant notations, HGVS strings, and genotype descriptions across all sections`
**Proposed:** `Scans the whole paper for allele symbols, variant notations (including HGVS, the standard way of writing sequence variants), and genotype descriptions.`
**Why:** `HGVS` is an undefined acronym; one short gloss keeps it usable for non-experts.

**Field:** `capabilities[3]` (`name`, `description`, `example_result`)
**Current:**
- name: `Validator-ready allele hints`
- description: `Preserves exact allele notation, associated-gene hints, organism context, and evidence records for the active allele validator; the extractor does not materialize final allele IDs.`
- example_result: `Returns allele mention envelopes with exact notation and evidence for downstream validator materialization`
**Proposed:**
- name: `Hands alleles to the validator`
- description: `Passes each proposed allele — with its exact notation, its likely gene, the organism, and the supporting quote — to the Allele Validation Agent, which does the authoritative database lookup. Settling on the final official allele ID is validator-owned; the extractor proposes, the validator confirms.`
- example_result: `Returns each proposed allele with its exact notation and supporting quote, ready for the Allele Validation Agent to confirm`
**Why:** Positive handoff framing. **Keeps "validator" and "validator-owned."**

**Field:** `data_sources[1]`
**Current:**
- name: `Allele validator binding`
- description: `The active allele validator checks retained mentions against Alliance allele rows after extraction and materializes allele identifier, symbol, and taxon fields when resolved.`
**Proposed:**
- name: `Allele Validation Agent`
- description: `The specialist that takes the proposed alleles and confirms each one against the Alliance database after extraction, filling in the official allele ID, symbol, and species when it finds a match.`
**Why:** "validator binding," "Alliance allele rows," and "materializes … fields" are mechanics. Keeps "validator."

**Field:** `limitations[3]`
**Current:** `Does not perform allele identity lookup itself; unresolved or conflicting identity remains visible as validator findings`
**Proposed:** `Does not perform the allele database lookup itself — that is the Allele Validation Agent's job; any unconfirmed or conflicting alleles stay visible to you as validator findings.`
**Why:** Reframes prohibition as handoff. **Keeps "does not perform" and "validator."**

---

### Disease Extractor (`packages/alliance/agents/disease_extractor/docs.yaml`) — EXTRACTOR (test-constrained)

**Field:** `capabilities[3]` (`name`, `description`, `example_result`)
**Current:**
- name: `Validator-ready disease selectors`
- description: `Preserves disease label/CURIE hints, relation names, data-provider selectors, subject context, and evidence records for active disease validators; ontology and vocabulary lookup remain validator-owned.`
- example_query: `Extract disease assertions with validator-ready context`
- example_result: `Returns pending disease annotation envelopes with disease, relation, provider, subject, and evidence selector inputs`
**Proposed:**
- name: `Hands diseases to the validator`
- description: `Passes each proposed disease — with the name as written, the gene-disease relationship, the data provider, the subject, and the supporting quote — to the Disease Validation Agent. Looking the disease up in the Disease Ontology and confirming the vocabulary is validator-owned; the extractor proposes, the validator confirms.`
- example_query: `Extract the diseases and pass them on for confirmation`
- example_result: `Returns each proposed disease, its relationship, provider, and subject, with the supporting quote, ready for the Disease Validation Agent to confirm`
**Why:** Positive handoff framing; drops "selectors," "envelopes," "selector inputs," "CURIE hints." **Keeps "validator" and "validator-owned."**

**Field:** `data_sources[1]`
**Current:**
- name: `Disease validator bindings`
- description: `Active disease validators resolve Disease Ontology terms, relation vocabulary, condition relation vocabulary, and data-provider selectors after extraction.`
**Proposed:**
- name: `Disease Validation Agent`
- description: `The specialist that takes the proposed diseases and confirms each one after extraction — matching the Disease Ontology term, the relationship vocabulary, and the data provider.`
**Why:** "validator bindings," "selectors" are mechanics. Keeps "validator."

**Field:** `limitations[3]`
**Current:** `Does not perform disease ontology or provider lookup itself; unresolved or conflicting identity remains visible as validator findings`
**Proposed:** `Does not perform the Disease Ontology or provider lookup itself — that is the Disease Validation Agent's job; any unconfirmed or conflicting diseases stay visible to you as validator findings.`
**Why:** Reframes prohibition as handoff. **Keeps "does not perform" and "validator."**

(The summary and the "diseases vs phenotypes" / "introduction-only excluded" limitations are clear — leave them.)

---

### Phenotype Extractor (`packages/alliance/agents/phenotype_extractor/docs.yaml`) — EXTRACTOR (test-constrained)

**Field:** `capabilities[3]` (`name`, `description`, `example_result`)
**Current:**
- name: `Validator-ready phenotype term context`
- description: `Preserves phenotype labels, organism/provider/taxon context, subject hints, and evidence for the active ontology validator; final ontology CURIEs are validator-owned.`
- example_result: `Returns phenotype assertion envelopes with label-backed term candidates and verified evidence for downstream validator materialization`
**Proposed:**
- name: `Hands phenotypes to the validator`
- description: `Passes each proposed phenotype — with the trait as written, the organism, the subject, and the supporting quote — to the phenotype term validator. Settling on the final official ontology term ID is validator-owned; the extractor proposes, the validator confirms.`
- example_result: `Returns each proposed phenotype with its supporting quote, ready for the validator to confirm the official term`
**Why:** Positive handoff framing; drops "context," "envelopes," "materialization," and the raw "CURIEs" as explanation. **Keeps "validator" and "validator-owned."**

**Field:** `data_sources[1].description`
**Current:** `The active phenotype validator resolves supported WB/MGI phenotype term labels or CURIEs after extraction and explicitly blocks unsupported provider/taxon mappings before lookup.`
**Proposed:** `The phenotype term validator confirms the official term for supported WormBase (WB) and Mouse Genome Informatics (MGI) phenotypes after extraction. Phenotypes from other databases aren't supported yet, so those are set aside rather than looked up.`
**Why:** "resolves … CURIEs" and "explicitly blocks … mappings" are mechanics/prohibition; the rewrite spells out WB and MGI and states the limit plainly. Keeps "validator."

**Field:** `limitations[3]`
**Current:** `Ontology term labels and hints are selector inputs only; typed ontology validation is handled by active domain-pack validator bindings`
**Proposed:** `The extractor proposes the trait wording only; confirming the official ontology term is the validator's job.`
**Why:** "selector inputs," "typed ontology validation," "domain-pack validator bindings" are all mechanics. Keeps "validator."

**Field:** `limitations[4]`
**Current:** `Does not use agr_curation_query directly; phenotype ontology validation is deferred to typed ontology term resolution`
**Proposed:** `Does not perform the phenotype term lookup itself — that is handled by the phenotype term validator after extraction.`
**Why:** `agr_curation_query` is a code identifier and is also a forbidden fragment for the guardrail test ("using agr_curation_query"); removing it is required for both voice and the test. **Keeps "does not perform" and "validator."**

(The summary and the "phenotypes vs diseases" / "wild-type baselines" limitations are clear — leave them.)

---

### Gene Expression Extraction (`packages/alliance/agents/gene_expression/docs.yaml`) — EXTRACTOR (test-constrained)

**Field:** `summary`
**Current:** `Extracts structured gene expression observations from uploaded PDFs, preserving relation and data-provider selector inputs for validator-owned verification.`
**Proposed:** `Reads an uploaded paper and pulls out where and when each gene is expressed — the tissues, cell types, developmental stages, and any sex-specific patterns — with the supporting quote, ready for the validators to confirm.`
**Why:** "relation and data-provider selector inputs for validator-owned verification" is dense jargon; the rewrite says what a curator actually gets. **Keeps "validator."**

**Field:** `capabilities[3]` (`name`, `description`, `example_result`)
**Current:**
- name: `Validator-ready selector capture`
- description: `Preserves expression relation, provider, taxon, gene, anatomy, stage, and reagent context as evidence-backed selector inputs while active validators own final controlled-vocabulary and provider verification.`
- example_query: `Extract expression observations with validator-ready context`
- example_result: `Returns expression annotations with relation/provider hints and verified evidence; final normalized fields are materialized by validators when active`
**Proposed:**
- name: `Hands expression details to the validators`
- description: `Passes each expression observation — the gene, the tissue or cell type, the developmental stage, the data provider, and the reagent used — to the specialist validators, each backed by its supporting quote. Confirming the official terms and the data provider is validator-owned; the extractor proposes, the validators confirm.`
- example_query: `Extract the expression observations and pass them on for confirmation`
- example_result: `Returns each expression observation with its supporting quote, ready for the validators to confirm the official terms`
**Why:** Positive handoff framing; drops "selector capture," "selector inputs," "materialized." **Keeps "validator" and "validator-owned."**

**Field:** `data_sources[1]`
**Current:**
- name: `Domain-pack validators`
- description: `Active validator bindings verify expression relation vocabulary and data-provider selectors after extraction. Planned gene/anatomy/stage/reference validation remains under-development metadata.`
**Proposed:**
- name: `Expression validators`
- description: `Specialist validators confirm the expression-relation vocabulary and the data provider after extraction. Confirming the gene, anatomy term, developmental stage, and reference against the database is still in development.`
**Why:** "validator bindings," "selectors," "under-development metadata" are mechanics. Keeps "validator."

**Field:** `limitations[2]` (`is_negative=true` in capability `[2].example_result`)
**Current (capabilities[2].example_result):** `Returns annotations with is_negative=true for statements like 'not detected in neurons'`
**Proposed:** `Flags these as negative results — for example "not detected in neurons."`
**Why:** `is_negative=true` is a raw field expression used as the explanation.

**Field:** `limitations[3]`
**Current:** `Ontology term ID resolution is handled by typed validator attachments or the Ontology Term Resolver Agent; this extractor only extracts human-readable labels`
**Proposed:** `The extractor records the term wording it finds; turning that into an official ontology ID is the validators' or the Ontology Term Resolver's job.`
**Why:** "typed validator attachments" / "human-readable labels" are mechanics. Keeps "validator."

(The reagent-capture and "wild-type only / no perturbations" / co-injection-marker limitations are clear and useful — leave them.)

---

### Curation Prep (`system_agent_docs.yaml` → `curation_prep`)

This is the only system-agent doc that is developer-first (the file header even says
the voice should be plain). It is heavy with "extraction envelopes,"
"deterministic anchor resolution," "adapter/profile structure," "materialized."

**Field:** `summary`
**Current:** `Collects upstream extraction envelopes, creates a curator review session, carries verified evidence into deterministic anchor resolution, and performs structural validation before curator review.`
**Proposed:** `Gathers everything the earlier agents found, checks that each item is complete and well-formed, and opens a ready-to-review session for you — keeping the supporting quotes attached to every item.`
**Why:** "extraction envelopes," "deterministic anchor resolution," "structural validation" are all mechanics; the rewrite says what the curator receives.

**Field:** `capabilities[0].description`
**Current:** `Collects prior extraction envelopes, scope confirmations, and evidence records into a single curation-prep request`
**Proposed:** `Pulls together what the earlier agents extracted, what you confirmed was in scope, and the supporting quotes, into one review package.`
**Why:** "extraction envelopes" / "curation-prep request" are mechanics.

**Field:** `capabilities[1].description`
**Current:** `Carries verified evidence references forward so the deterministic anchor-resolution pipeline starts from the same evidence the extractor kept`
**Proposed:** `Keeps the supporting quotes attached to each item, so the later automated steps work from exactly the evidence the extractor found.`
**Why:** "evidence references," "deterministic anchor-resolution pipeline" are mechanics.

**Field:** `capabilities[1].example_result`
**Current:** `Returns structured candidates whose field values stay linked to upstream evidence records and ambiguity notes`
**Proposed:** `Returns the items for review, each still linked to its supporting quote and any notes about uncertainty.`
**Why:** "structured candidates," "upstream evidence records" are mechanics.

**Field:** `capabilities[2].description`
**Current:** `Checks adapter/profile structure and flags incomplete or uncertain payloads before the curator sees the review session`
**Proposed:** `Checks that each item is complete and well-formed, and flags anything incomplete or uncertain before you open the review session.`
**Why:** "adapter/profile structure" and "payloads" are code terms.

**Field:** `capabilities[2].example_result`
**Current:** `Returns only in-scope candidates plus warnings when fields still need curator attention`
**Proposed:** `Returns only the in-scope items, plus warnings where a field still needs your attention.`
**Why:** "candidates"/"fields" tidy to plain "items."

**Field:** `limitations[3]`
**Current:** `Downstream deterministic services still own anchor resolution, normalization, and persistence`
**Proposed:** `The later automated steps still handle matching items to official records and saving them.`
**Why:** "deterministic services," "anchor resolution," "normalization," "persistence" are all mechanics.

(`task_input` in the same file is already plain and curator-friendly — leave it.)

---

## 3. Cross-cutting note (apply judgement, not a blanket change)

**`CURIE`** appears unexpanded in several docs (allele, ontology_term, reference,
agm, disease_extractor) and reads as developer shorthand. It almost always appears
as a *secondary detail* (e.g. "Alliance CURIE," "matched CURIE"), which the standard
permits. Recommendation: on first appearance per doc, say "ID (also called a CURIE,
e.g. MGI:3689906)" or simply "ID," rather than leading with the bare acronym. This
is a light touch and is **not** counted in the 12 "need changes" agents above unless
the doc had other issues; flagging it here so Chris can decide whether to sweep it.

---

## 4. Agents already fine (no changes proposed)

These read cleanly for a biocurator as-is (the 7 recently-relocated agent.yaml docs
land here, as expected, plus the formatters and the two simple lookups):

1. **Gene Validation** (`gene`) — plain, concrete examples, clear limitations.
2. **PDF Extraction** (`pdf`) — "indexed in Weaviate" is a minor data-source label, acceptable as a secondary detail; prose is clear. (Optional: could rename the data source from "indexed in Weaviate" to "your uploaded paper" — borderline, not required.)
3. **Chat Output Formatter** (`chat_output`) — short and plain.
4. **CSV Output Formatter** (`csv_formatter`) — short and plain.
5. **JSON Output Formatter** (`json_formatter`) — short and plain.
6. **TSV Output Formatter** (`tsv_formatter`) — short and plain.
7. **AGM Validation** (`agm`) — clear; AGM is spelled out on first use. (`matched_id`/`matched_label` appear once in an example result — minor; optional tidy to "the matching record and label.")
8. **Data Provider Validation** (`data_provider`) — clear; `NCBITaxon` appears as a secondary detail, acceptable. (Optional gloss: "species, identified by its NCBI taxonomy ID.")
9. **Controlled Vocabulary Validation** (`controlled_vocabulary`) — clear and plain.
10. **Ontology Term Resolver** (`ontology_term`) — uses `CURIE` and term-type names (DOTerm, ECOTerm, etc.) but as secondary technical detail a curator resolving terms would recognize; acceptable. (Optional: gloss CURIE on first use.)
11. **Experimental Condition Validation** (`experimental_condition`) — clear; `CHEBITerm` appears once as a secondary detail, acceptable.
12. **Subject Entity Validation** (`subject_entity`) — clear; `subject_type` appears but is described in plain terms around it.
13. **Reference Validation** (`reference`) — clear; PMID/DOI/AGRKB are standard curator identifiers.
14. **Task Input** (`task_input` in `system_agent_docs.yaml`) — already plain and curator-friendly.

(Note: the 7 relocated-from-agent.yaml docs — agm, data_provider, controlled_vocabulary,
ontology_term, experimental_condition, subject_entity, reference — all land here. As
expected, they were already fairly curator-friendly and need no changes, only the
optional CURIE/NCBITaxon glosses noted above.)

---

## 5. The extractor / guardrail-test tension — RESOLVED

The standing framing preference wants validators presented as the **stronger,
specialized resolver** and extractors framed by the **positive division of labor**,
not by prohibitions. The original automated test pulled the other way — it *required*
each of the 5 extractor docs to keep the word "validator" plus one of "does not
perform" / "validator-owned," and to avoid a set of forbidden lookup phrases.

**Decision (Chris):** the prohibition framing is legacy — remove it outright rather
than preserve it to satisfy the test. As shipped in `c64ba126`:

- All 5 extractor docs are **fully prohibition-free.** They lead with the positive
  handoff ("the extractor proposes; the validator confirms and settles the official
  ID") and present the validator as **the stronger specialist that authoritatively
  confirms** — they no longer contain "validator-owned" or "does not perform."
- The test `test_extractor_documentation_keeps_validator_boundary_clear` was renamed
  to `test_extractor_documentation_references_validator_handoff`. Its
  `forbidden_fragments` set and its `assert "does not perform" … "validator-owned"`
  line were **removed**. It now asserts only that each extractor doc still mentions
  the `validator` handoff — confirming the division of labor is documented without
  dictating prohibition wording.
- The parity baseline JSON was updated to the new text, and the branch stays green.
