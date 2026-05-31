# Cross-Domain Extractor Migration — Autonomous First-Pass Runbook

Date: 2026-05-31. Author: Claude (for Chris, to execute autonomously while he drives ~2h).
Status: ACTIVE autonomous runbook. This is the document I (Claude) follow when no one is
watching. It is self-contained on purpose so it survives a context reset.

Related: `2026-05-31-builder-inline-validation-and-cross-domain-migration.md` (the migration
sequence + audit). That doc is the architecture plan; THIS doc is the safe, do-it-now task.

---

## 0. Autonomy contract (read this first, every time)

**Goal of this autonomous session:** produce, for each curation data type that still uses the
envelope pattern (gene, disease, chemical_condition, allele, phenotype), a **first-pass
"approach" design doc** — the use-case spec we are missing because there is no Daniela-style
document for these types. Each doc is grounded in three real sources, NOT invented:

1. the **LinkML model** (`temp_agr_curation_schema/model/schema/*.yaml`),
2. the **AWS curation database** (readonly) — what curators *actually* produce,
3. the **literature** (PMID references / evidence), where reachable.

**This session is DOCS + READ-ONLY DB QUERIES ONLY.** It is the "come up with the best
approach for a first pass" work Chris asked for — the prerequisite for any code migration.

### HARD STOPPING CONDITIONS — pause and leave a note, do NOT proceed past these
- **Do NOT change extractor/runtime CODE** (no edits under `backend/src`, `packages/.../python`,
  `packages/.../domain_packs/*/domain_pack.yaml`, agent schemas/prompts). Code migration is a
  separate, reviewed step. This session writes ONLY under `docs/design/`.
- **Read-only DB access only.** Only `SELECT`. Light queries (LIMITs, counts). Never write to
  the curation or literature DBs. Never print DB credentials.
- **Stop on any genuine design decision** (ambiguous LinkML, conflicting curation reality vs
  schema, a required field with no obvious extraction source). Write it under `## Open
  questions for Chris` in that type's doc and move on — do not guess and bake it in as settled.
- **Stop if a grounding source is unavailable** (e.g., literature DB tunnel down). Note it in
  the doc, proceed with the sources you do have.
- **Commit each approach doc as you finish it** (one commit per type) so work is saved and Chris
  can review async from git. Use explicit `git add <file>`; never `-A`/`.`. Push to `main`.
- If anything here conflicts with a newer instruction from Chris, Chris wins.

When all five type docs exist + are committed, STOP. Do not begin code migration. Leave a
summary in `## Progress log` below and wait.

---

## 1. Grounding sources (how to query them)

**LinkML model** (already cloned, read-only reference @ commit 1b11d088):
`temp_agr_curation_schema/model/schema/`. Type → primary schema file:
- gene → `gene.yaml` (class `Gene`); shared identity in `core.yaml` (`BiologicalEntity`,
  `primary_external_id`, slot annotations).
- disease → `phenotypeAndDiseaseAnnotation.yaml` (class `DiseaseAnnotation` / `*DiseaseAnnotation`).
- phenotype → `phenotypeAndDiseaseAnnotation.yaml` (class `PhenotypeAnnotation`).
- allele → `allele.yaml` (class `Allele`).
- chemical_condition → `controlledVocabulary.yaml` / `core.yaml` ExperimentalCondition +
  chemical (ZECO/ChEBI); confirm exact classes from the pack + schema.
- (reference: gene_expression → `expression.yaml`, already migrated — the template.)

**AWS curation DB** (readonly, CONFIRMED reachable from the backend container; 189 public
tables). Query via the backend container using the env var (do NOT hardcode/print the URL):
```
incus exec symphony-main -- bash -lc 'docker exec agrmainsandbox-backend-1 bash -lc '"'"'python3 -c "
import os, psycopg2
c=psycopg2.connect(os.environ[\"CURATION_DB_URL\"]); cur=c.cursor()
cur.execute(\"<SELECT ... LIMIT 5>\")
for r in cur.fetchall(): print(r)
c.close()"'"'"''
```
Useful tables seen: `geneexpressionannotation`, `geneexpressionexperiment`, `expressionpattern`
(gene_expression). For others, discover with:
`select table_name from information_schema.tables where table_schema='public' and table_name ilike '%disease%'` (etc.).
Goal: read 5–20 real curated rows per type to learn the fields curators actually fill, the
controlled-vocabulary CURIEs used, and which slots are reliably present.

**Literature**: `LITERATURE_DB_URL` (readonly postgres) — tunnel may be DOWN this session
(host.docker.internal:6286). If `psycopg2.connect` fails, note it and skip. ELASTICSEARCH
`references_index` (`ELASTICSEARCH_HOST`, port 443) is the reference search index used by
`reference_validation`. Only needed to confirm how PMIDs/evidence are sourced.

**The reference implementation** (gene_expression, now structurally clean — copy its shape):
- pack metadata: `packages/alliance/domain_packs/gene_expression/domain_pack.yaml`
- conversion + builder materializer: `.../gene_expression/conversion.py`
  (`materialize_gene_expression_builder_state`, `_metadata_ref_findings`, projection checks)
- golden fixtures: `.../gene_expression/fixtures/tmem67_pending.yaml`

---

## 2. Lessons from today (bake these into every approach doc as constraints)

These are the gene_expression bugs we just fixed; the other types will need the same handling.
1. **`metadata_refs` are relative to the extraction-metadata namespace** (`raw_mentions[N]`,
   `evidence_records[N]`), resolved against `envelope.metadata.extraction_metadata`. Never write
   absolute `extraction_metadata.<path>` refs. (commit 98a9b3d3)
2. **Object status lifecycle**: `PENDING` = "not yet validated by the automated validator".
   Automated validation legitimately advances a resolved object to `VALIDATED`; that is unrelated
   to curator review. Do NOT add an "objects must be pending" contract check. (commit 91f7a784)
3. **Validator execution errors are non-fatal**: a validator that can't run becomes a distinct
   `domain_pack.validator_error` OPEN finding (vs `validator_unresolved` = ran, no match); the
   extraction still persists. (commit abfe55ed)
4. **Mirror fields use declared `materializes_to_field_paths`**: when the LinkML model requires
   one field to mirror another (gene_expression: `entity_assayed` must equal the subject gene),
   declare it as field metadata so the resolved value propagates automatically — do NOT special-
   case it in code. (commit 2ec6b3b9)
5. **The extraction-output → DomainEnvelope seam**: the extractor emits structured
   `ExtractionEnvelopeMetadata` (raw_mentions, evidence_records, …); the generic converter nests
   it under `metadata.extraction_metadata` and stamps platform keys (source_kind, run_summary…)
   at top level. Refs/evidence resolve against the `extraction_metadata` namespace.

---

## 3. Per-type approach-doc template

Write each as `docs/design/data-type-approaches/<type>-first-pass-approach.md` with sections:

```
# <Type> extraction — first-pass approach (derived, no use-case doc)

## Target LinkML
- Primary class(es) + file; required slots; key relations; controlled-vocab ranges (CURIE prefixes).
- Identity/provenance slots (primary_external_id, data_provider, single_reference, evidence).

## What curators actually produce (AWS curation DB grounding)
- Table(s) queried + row counts; 5–20 sampled rows summarized.
- Which fields are reliably present; the real CURIE namespaces; common shapes/edge cases.
- Anything in the DB that the LinkML alone wouldn't tell you.

## Curatable objects to extract (the envelope)
- Object type(s), per-object fields (payload paths), required vs optional.
- Subject entities (gene/allele) and how they resolve.
- Mirror constraints -> materializes_to_field_paths (if any LinkML "X must match Y").

## Validators needed
- Gene/allele resolution, ontology-term resolution (which ontologies), reference (PMID).
- Map each validatable field -> validator binding (mirror gene_expression's bindings).

## Evidence & provenance
- raw_mentions / evidence_records; how PMID + verified quotes attach.

## Builder-pattern mapping
- Candidate staging shape; finalize tool; per-domain materializer responsibilities.
- What differs from gene_expression; what can reuse the shared engine.

## Open questions for Chris
- Every genuine decision/ambiguity. Do not guess these.

## Sources
- LinkML files + classes read; curation DB tables + query date; literature status.
```

---

## 4. Execution order + the loop

Order (simplest/most-similar first → hardest): **gene → disease → phenotype → allele →
chemical_condition.** (gene is the canary: it's the simplest entity and the basis the others
reference.)

For each type, the loop:
1. Read the LinkML class(es) for the type (section 1 mapping).
2. Discover + query the curation DB table(s) for that type; sample real rows (read-only).
3. Read the existing envelope pack (`packages/alliance/domain_packs/<type>/domain_pack.yaml`
   + any `packages/alliance/python/.../domain_packs/<type>/conversion.py`) to see the current
   approach and existing validator bindings.
4. Write the approach doc from the template, grounded in 1–3, with explicit open questions.
5. `git add` that one doc + commit + push. Update `## Progress log` below.
6. Move to the next type. Honor every stopping condition.

Do NOT touch code. Do NOT migrate. This session ends with five committed approach docs.

---

## 5. Progress log (update as you go)

- 2026-05-31: Runbook created. Confirmed curation DB readonly access (189 tables) + LinkML
  clone present; literature DB tunnel down this session. Starting with `gene`.
