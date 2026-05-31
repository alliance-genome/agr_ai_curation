# Phenotype — builder-migration approach (Phase 3)

Date: 2026-05-31. Grounding pass per `docs/design/2026-05-31-cross-domain-first-pass-runbook.md`
§3 (grounding) / §4 (recipe) / §5 (invariants). Reference shape: `gene_expression` (proven, clean).
This is a grounding doc only — no code written. It justifies the Phase-3 code pass.

Three real sources used:
1. **LinkML** — `temp_agr_curation_schema/model/schema/phenotypeAndDiseaseAnnotation.yaml` (+ `core.yaml`),
   read-only clone @ commit `1b11d088`.
2. **AWS curation DB** — read-only, SELECT-only, via the backend container `CURATION_DB_URL`
   (URL never printed). 189-table production curation DB; phenotype tables hold real curated data.
3. **Existing envelope pack** — `packages/alliance/domain_packs/phenotype/domain_pack.yaml`,
   `packages/alliance/python/src/agr_ai_curation_alliance/domain_packs/phenotype/__init__.py`
   (+ `export.py`), and `packages/alliance/agents/phenotype_extractor/{agent.yaml,prompt.yaml}`.

> NOTE ON NAMING: the agent folder is `phenotype_extractor` (not `agents/phenotype/`), and the
> existing domain converter lives in `domain_packs/phenotype/__init__.py` (not `conversion.py`).
> gene_expression's materializer lives in `domain_packs/gene_expression/conversion.py`. The
> per-type recipe (§4) names `domain_packs/<type>/conversion.py`; for phenotype this either means a
> new `conversion.py` module or appending the materializer into the existing `__init__.py`. See
> Open Questions.

---

## 1. Target LinkML class + slots

**Parent (extraction target / curatable unit): `PhenotypeAnnotation`** (`is_a: Annotation` →
`SingleReferenceAssociation`). Three concrete write subtypes, chosen by subject range:

| Concrete class | Subject (`phenotype_annotation_subject`) range | Extra slots |
|---|---|---|
| `GenePhenotypeAnnotation` | `Gene` | `sgd_strain_background` (optional) |
| `AllelePhenotypeAnnotation` | `Allele` | `inferred_gene` (opt), `asserted_genes` (opt, multivalued) |
| `AGMPhenotypeAnnotation` | `AffectedGenomicModel` | `inferred_gene`, `inferred_allele`, `asserted_genes`, `asserted_alleles` (all opt) |

The matching ingest/DTO shape (what the curation pipeline actually consumes) is
`PhenotypeAnnotationDTO` → `Gene|Allele|AGMPhenotypeAnnotationDTO`.

### Key slots (PhenotypeAnnotation + parent Annotation)

**Required:**
- `phenotype_annotation_object` — *attribute*, `range: string`, **required: true**. This is the
  **free-text phenotype statement** ("the label of an individual phenotype term … or the
  post-composed statement"). It is the primary curated value and is **always populated** in the DB.
- `phenotype_annotation_subject` — **required: true**, `range: BiologicalEntity` (narrowed to
  Gene/Allele/AGM in subtypes). The biological entity the phenotype is asserted on.
- `single_reference` — **required: true** (`slot_usage` on PhenotypeAnnotation): "the reference in
  which the phenotype association was asserted/reported." Inherited from `Annotation`/SingleReferenceAssociation.

**Optional / context:**
- `phenotype_terms` — `range: PhenotypeTerm`, **multivalued**, NOT marked required at the slot
  level. `slot_usage.values_from: [HP, MP, WBPhenotype, ZP, APO, FBcv]`. Examples in schema:
  `HP:0002487`, `WBPhenotype:0000180`, `MP:0001569`. (Note: the existing pack treats
  `phenotype_terms[0]` as required for export-readiness — a pack convention, not a LinkML constraint.)
- `negated` — `range: boolean`, optional (polarity qualifier).
- `cross_reference` — optional object ref.
- `date_created` — optional (curation date at source MOD).
- `related_notes` — optional, CV "Phenotype annotation note types".
- `condition_relations` — inherited from `Annotation`, `range: ConditionRelation` multivalued
  (experimental conditions: anatomy / stage / chemical / taxon context). Optional.
- `mod_internal_id`, `primary_external_id`, `unique_id`, `curie`, `data_provider`,
  `data_provider_cross_reference` — identity/provenance, optional for new annotations.

### "X must match Y" constraints / mirrors

- **No `materializes_to_field_paths` mirror is required.** Unlike gene_expression (subject gene →
  `entity_assayed`), the phenotype subject is the canonical subject directly; there is no second
  field that must mirror it. The existing `domain_pack.yaml` declares **no** `materializes_to_field_paths`
  (confirmed: `grep` returns none). The only structural rule is **subject-range → concrete subtype**
  (Gene→Gene-, Allele→Allele-, AGM→AGM-PhenotypeAnnotation), which is a routing invariant, not a
  field mirror.
- **Evidence (ECO) is NOT modeled on PhenotypeAnnotation.** Contrast: `DiseaseAnnotation` has
  required `evidence_codes` (multivalued ECO). PhenotypeAnnotation has **no `evidence_codes` slot**
  and the DB has **no phenotype↔ECO link table** (confirmed below). Phenotype "evidence" =
  the single supporting reference + (in this pack) record_evidence-verified paper quotes.
- `phenotype_terms` ranges over `PhenotypeTerm` whose CURIE must come from a phenotype ontology
  (`values_from` list above) — a controlled-vocabulary constraint resolved by the ontology validator.

---

## 2. Curation-DB reality (read-only, SELECT-only; URL never printed)

**Discovered phenotype tables** (`information_schema.tables`, `ilike '%phenotype%'`):
`phenotypeannotation`, `genephenotypeannotation`, `allelephenotypeannotation`,
`agmphenotypeannotation`, `phenotypeannotation_ontologyterm`, `phenotypeannotation_conditionrelation`,
`phenotypeannotation_note`, `agmphenotypeannotation_allele`, `agmphenotypeannotation_gene`,
`allelephenotypeannotation_gene`, `genegeneticinteraction_phenotypesortraits`.

**Row counts (real production volume):**
- `phenotypeannotation` = **1,662,327**
- `genephenotypeannotation` = 468,808 · `allelephenotypeannotation` = 499,448 · `agmphenotypeannotation` = 694,071
- `phenotypeannotation_ontologyterm` = 1,447,607 (link rows); distinct annotations with ≥1 term = **1,160,519** (~70%)
- `phenotypeannotation_conditionrelation` = 415,583 (~25% of annotations carry experimental conditions)

**`phenotypeannotation` columns:** `id, datecreated, dateupdated, db*, internal, obsolete, curie,
primaryexternalid, modinternalid, uniqueid, phenotypeannotationobject, createdby_id, updatedby_id,
evidenceitem_id, crossreference_id, relation_id, dataprovider_id, dataprovidercrossreference_id`.

**Reliably-present slots (real fill rates, sampled):**
- `phenotypeannotationobject` (free-text statement): **200,000 / 200,000 non-null** — always filled.
- `evidenceitem_id` (the cited reference): **200,000 / 200,000 non-null** — always filled.
- `dataprovider_id`: always present in sampled rows.
- Ontology term: present on ~70% of annotations (free-text-only annotations are common and valid).

**3–5 real example rows (free-text statement, `phenotypeannotation`):**
| id | phenotypeannotationobject (free text) | has evidence ref | has data provider |
|---|---|---|---|
| 200019101 | `absent teeth` | yes | yes |
| 200019102 | `lung inflammation` | yes | yes |
| 200019103 | `osteopetrosis` | yes | yes |
| 200019104 | `premature death` | yes | yes |
| 200019125 | `increased gastrointestinal tumor incidence` | yes | yes |

**Real subject CURIE namespaces (split_part of `biologicalentity.primaryexternalid`):**
- Gene subjects (`genephenotypeannotation`): **HGNC** (442,925), SGD, Xenbase, RGD, ZFIN.
- Allele subjects (`allelephenotypeannotation`): **SGD** (288,884), **FB** (181,090), WB, RGD.
- AGM subjects (`agmphenotypeannotation`): **MGI** (400,195), **ZFIN** (291,909), RGD.

**Real phenotype-term ontology namespaces (`phenotypeannotation_ontologyterm` → `ontologyterm.curie`):**
- **APO** (592,017) · **HP** (442,925) · **MP** (407,402) · **XPO** (5,263).
- Real term examples: `MP:0011091` "prenatal lethality, complete penetrance", `MP:0005402`
  "abnormal action potential", `MP:0010733` "abnormal axon initial segment morphology".
- **IMPORTANT MISMATCH:** the LinkML `values_from` lists `HP, MP, WBPhenotype, ZP, APO, FBcv`, but
  the live DB top namespaces are **APO/HP/MP/XPO**. The existing pack's *active* ontology validator
  only enables **WBPhenotype (WB)** and **MP (MGI)** — neither WBPhenotype (no WBPhenotype rows in
  the sampled link table top-15) nor XPO/APO/HP appear as active accepted prefixes. See Open Questions.

**Reference linkage:** `phenotypeannotation.evidenceitem_id` → `informationcontententity.id`; the
cited reference surfaces as `informationcontententity.curie` = **`AGRKB:101000000675023`** form
(Alliance-minted AGRKB CURIEs), NOT a bare PMID. (The local `reference` table only has
`shortcitation, id`; the curie lives on the ICE parent.)

**Evidence/ECO:** confirmed **no** `phenotype…eco` / `phenotypeannotation…evidence` link table
exists. Phenotype has no ECO evidence-code model in the DB (unlike disease).

---

## 3. Curatable objects / fields (from the existing envelope pack — REUSED)

`pack_id: agr.alliance.phenotype`. `curatable_unit_object_type: PhenotypeAnnotation`.
`semantic_source: domain_envelope.objects`. `legacy_semantic_lists: []`. Object roles:
`curatable_unit`, `validated_reference`, `metadata_only`.

**Object: `PhenotypeAnnotation`** (role `curatable_unit`, model `PhenotypeAnnotationPayload`):
- `annotation_kind` (string, required, protected — routing invariant)
- `phenotype_annotation_object` (string, **required**) — free-text phenotype statement (LinkML attribute)
- `phenotype_annotation_subject` (object_ref → `PhenotypeSubject`, required; binding `phenotype_subject_entity_validator`)
- `phenotype_terms[0]` (object_ref → `PhenotypeTerm`, required; binding `phenotype_term_ontology_validator`)
- `phenotype_terms[0].curie` (string, optional fast-path), `.label` (protected display-only),
  `.source_mentions[0]`, `.ontology_lookup_hint.{data_provider,taxon_id,evidence_record_id}`,
  `.export_state`, `.write_blocked_reason`
- `single_reference` (object_ref → `Reference`, required; binding `phenotype_reference_validator`)
- `evidence_quote` (object_ref → `EvidenceQuote`, required)
- `evidence_record_ids[0]` (string, required)
- `negated` (boolean) — missing = explicit `false`
- `source_mentions[0]` (protected)

**Object: `PhenotypeSubject`** (role `validated_reference`, model `PhenotypeSubjectPayload`,
`validation_state: pending_entity_resolution`): `resolution_state` (enum
`PhenotypeSubjectResolutionState`: resolved / pending_entity_resolution / blocked_missing_subject),
`subject_identifier`, `subject_label`, `subject_type`, `taxon` (required, NCBITaxon CURIE).

**Object: `PhenotypeTerm`** (role `validated_reference`, model `PhenotypeTermPayload`,
`validation_state: pending_ontology_resolution`): `resolution_state` (enum
`PhenotypeTermResolutionState`: resolved / pending_ontology_resolution), `curie` (optional),
`label`, `source_mentions[0]`, `ontology_lookup_hint.{data_provider,taxon_id,evidence_record_id}`,
`export_state`, `write_blocked_reason`.

**Object: `Reference`** (role `validated_reference`, `pending_reference_resolution`):
`reference_id` (integer), `title`, `filename`.

**Object: `EvidenceQuote`** (role `metadata_only`): `evidence_record_id` (req), `entity`,
`verified_quote` (req), `page`, `section`, `subsection`, `chunk_id`, `figure_reference`.

**Export/write posture (unchanged by migration):** `export_blocker_policy.status: blocked`. Pending
phenotype annotations are non-exportable/non-writable until subject subtype + reference + ontology
term row are resolved. The builder migration changes the **extraction mechanism only**, not this
curation/export posture.

---

## 4. Validator bindings (REUSE — list)

From `domain_pack.yaml` `metadata.validator_bindings`:

**Active:**
- `phenotype_term_ontology_validator` — agent `agr.alliance/ontology_term_validation`; applies to
  `PhenotypeTerm`. Inputs: payload `curie`/`label` + context-only `ontology_lookup_hint.*` +
  evidence_record `verified_quote`/`chunk_id`/`section`; literals `ontology_family: phenotype`,
  `accepted_prefixes: [MP, WBPhenotype]`, and `provider_taxon_ontology_mappings` for **WB→WBPhenotypeTerm**
  and **MGI→MPTerm** (both `live_db_term_type_verified: true`). `required: true`, `blocking: false`,
  `allow_opt_out: true`, `curator_override.allowed: false`.

**Under development (bindings declared but not active):**
- `phenotype_pending_envelope_validator` — package-scoped pending-envelope data check (waits on a
  package phenotype validation agent).
- `phenotype_subject_entity_validator` — agent `agr.alliance/subject_entity_validation`; applies to
  `PhenotypeSubject` (`subject_identifier`, `subject_type`). Routes subject_type → gene/allele/AGM.
- `phenotype_reference_validator` — agent `agr.alliance/reference_validation`; applies to `Reference`
  (`reference_id`, `curie`, `title`); metadata-only until materialization/export activated.

**Domain validators** (`metadata.validators`): active = `phenotype.domain_envelope_shape`,
`phenotype.pending_envelope_policy`, `phenotype.no_legacy_semantic_lists`,
`phenotype_term_ontology_validator`. Under development = `phenotype.subject_entity_resolution`,
`phenotype.additional_provider_ontology_mappings`, `phenotype.export_submission_projection`
(blocked_by ALL-425), `phenotype.extractor_output_migration` (blocked_by ALL-412 — **this is the
slot this builder migration fills**).

> Reuse all bindings as-is. The migration must keep these binding IDs and `validator_binding_id`
> field metadata intact (invariant §5.5: builder output DOES run validators).

---

## 5. Evidence model

- **No ECO / evidence_codes** on PhenotypeAnnotation (LinkML + DB both confirm). Do not invent an
  ECO field. Phenotype evidence = (a) the **single supporting reference** (`single_reference`, always
  present in the DB as an AGRKB CURIE on the ICE parent), plus (b) **record_evidence-verified paper
  quotes** captured as the pack's `EvidenceQuote` metadata-only objects + `evidence_record_ids`.
- The builder finalize path (mirroring gene_expression) requires each finalized candidate to carry
  non-empty `evidence_record_ids` that resolve to active-run `metadata.evidence_records` entries
  with a non-blank `verified_quote` (enforced by `finalize_builder_extraction`'s provenance gate +
  the materializer's per-candidate checks).
- Optional experimental context (anatomy / life-stage / chemical / taxon) is modeled in LinkML as
  `condition_relations` → `ConditionRelation` → `ExperimentalCondition` and is present on ~25% of DB
  rows. The existing pack does NOT currently surface condition relations as a curatable field; keep
  that scope for this pass unless Chris wants it added (Open Question).

---

## 6. Builder mapping (the Phase-3 code shape — copy gene_expression)

Mirror gene_expression file-by-file (runbook §2/§4). Target tool/function names:

**Staging tools** (agent stages candidates; register in `bindings.yaml`, thin adapters over
`ExtractionBuilderWorkspace`, prefer a per-domain module rather than piling into `agr_curation.py`):
- `stage_phenotype_observation` — stage one phenotype assertion candidate (free-text statement +
  pending subject ref + pending phenotype term(s) + evidence_record_ids + resolver selection refs).
- `patch_phenotype_observation` — bounded repairs to a staged candidate (controlled-selector
  patches carry `resolver_call_id` provenance).
- `discard_phenotype_observation` — mark one staged candidate discarded (trace retained).
- `list_staged_phenotype_observations` — compact builder-state summaries.
- `finalize_phenotype_extraction` — finalize tool; **`metadata.builder_finalization: true`** in its
  bindings entry (this flag is how `streaming_tools._builder_finalization_tool_names()` detects it —
  a domain-pack edit, NOT a platform edit, per Phase-0).

**Materializer** — add `materialize_phenotype_builder_state(*, workspace, candidate_ids,
evidence_records, resolver_entry_lookup, produced_by="phenotype_extractor")` in
`domain_packs/phenotype/conversion.py` (or `__init__.py` — see Open Questions), mirroring
`materialize_gene_expression_builder_state` (conversion.py:630). It reads workspace candidates and
emits the extraction-output payload `{summary, curatable_objects, metadata}` where each
`CuratableObjectEnvelope` has:
- `object_type = "PhenotypeAnnotation"`, `object_role = "curatable_unit"`, `pending_ref_id`,
  `model_ref = "PhenotypeAnnotationPayload"`, schema_ref to PhenotypeAnnotation,
  `definition_state: in_development`.
- `payload` = staged fields → `{annotation_kind, phenotype_annotation_object,
  phenotype_annotation_subject(→PhenotypeSubject pending), phenotype_terms[…](→PhenotypeTerm pending),
  single_reference(→Reference pending), negated, source_mentions[…]}` + the pending
  `PhenotypeSubject`/`PhenotypeTerm`/`Reference`/`EvidenceQuote` validated_reference/metadata_only
  objects.
- `evidence_record_ids` = the candidate's verified evidence ids.
- **`metadata_refs` RELATIVE** (invariant §5.1): `{"metadata_path": "raw_mentions[N]", "role":
  "source_mention"}` + `{"metadata_path": "evidence_records[N]", "role": "verified_evidence"}` for
  each retained evidence id. **Never** write absolute `extraction_metadata.<path>` refs; the generic
  converter nests `metadata` under `metadata.extraction_metadata`. Add a `_metadata_ref_findings`
  check mirroring gene_expression (conversion.py:1641) keyed
  `alliance.phenotype.metadata_refs_missing`.

**Finalize adapter** — add a thin `finalize_phenotype_extraction` impl mirroring
`_finalize_gene_expression_extraction_impl` (agr_curation.py:6026): validate input schema, snapshot
run-scoped evidence records + resolver ledger, then delegate to
`finalize_builder_extraction(workspace=…, candidate_ids=…,
materialize=<phenotype materialize-with-events wrapper>, evidence_records=…,
resolver_entry_lookup=resolver_ledger.get, materialized_candidate_prefix="phenotype-annotation-envelope")`.
All structural/provenance/idempotency control flow stays in the shared
`finalize_builder_extraction` (builder_finalization.py:127) — domain code supplies only the
materializer.

**Agent** — in `agents/phenotype_extractor/agent.yaml`: set `output_schema: null` (remove
`PhenotypeResultEnvelope`); swap the tool list from the current
`[search_document, read_chunk, read_section, read_subsection, record_evidence, get_agent_contract,
agr_species_context_lookup]` to the builder set (gene_expression parity): add the evidence-management
tools (`list_recorded_evidence`, `get_recorded_evidence`, `attach_evidence_to_object`,
`detach_evidence_from_object`, `discard_recorded_evidence`, `update_recorded_evidence_metadata`),
the selector-resolution tools (`search_domain_field_terms`, `inspect_ontology_term`,
`resolve_domain_field_term`), and the new `stage/patch/discard/list/finalize_phenotype_*` tools.
Rewrite `prompt.yaml` into a builder tool-loop (record evidence → stage observations → resolve
selectors → finalize) copying gene_expression's prompt structure.

**Domain pack** — `domain_pack.yaml` objects/fields/validator bindings already declared (§3/§4);
no `materializes_to_field_paths` needed (no mirror). Keep `export_blocker_policy` blocked.

**Detection** — `finalize_phenotype_extraction`'s `builder_finalization: true` flag is sufficient
(Phase-0 generalized detection); no platform edits.

**Fixtures/tests** — add a golden pending fixture with RELATIVE metadata_refs + unit/contract tests
mirroring the gene_expression pack tests. The existing
`fixtures/tool_verified_pending.yaml` is the current envelope fixture to model the builder golden on.

---

## 7. Open questions for Chris (do NOT guess)

1. **Ontology namespace coverage mismatch.** Live DB phenotype-term namespaces are **APO, HP, MP,
   XPO** (top-4), and the LinkML `values_from` is `HP, MP, WBPhenotype, ZP, APO, FBcv` — but the
   pack's *active* ontology validator only enables **WBPhenotype (WB)** and **MP (MGI)**. WBPhenotype
   did not appear in the sampled top-15 link namespaces, and APO/HP/XPO (the highest-volume real
   namespaces, incl. all SGD/yeast APO and human HP) are in the validator's `under_development` list
   (RGD/HGNC/ZFIN/FB/SGD). Which provider/ontology pairs should the builder migration ship as
   *active*? Shipping only WB+MP would mean the builder cannot resolve the majority of real
   phenotype terms (APO/HP). Recommend at least adding **HGNC→HP** and **SGD→APO**, but this needs
   live `ontologyterm` term-type grounding verification first (per `phenotype.additional_provider_ontology_mappings`).

2. **Materializer file location.** Recipe §4 says `domain_packs/<type>/conversion.py`, but phenotype's
   existing converter is `domain_packs/phenotype/__init__.py` (gene_expression uses a dedicated
   `conversion.py`). Add a new `phenotype/conversion.py` for `materialize_phenotype_builder_state`
   (parity with gene_expression), or append into `__init__.py`? Recommend a new `conversion.py` for
   parity + to keep the existing fixture-based envelope builder untouched.

3. **Reference shape: AGRKB vs PMID.** In the DB the phenotype reference surfaces as an **AGRKB**
   CURIE on `informationcontententity`, not a bare PMID; the pack's `Reference` object models
   `reference_id` (integer) / `title` / `filename` and the reference validator accepts `pmid`/`doi`/
   `curie`. What is the canonical pending-reference identity the builder should stage for a paper
   under extraction — the document's PMID/DOI, an AGRKB curie, or the integer `reference.id`? The
   extraction sees the loaded PDF; mapping to the durable AGRKB row is a resolver concern. Confirm
   the expected pending-reference field shape so the materializer fills it consistently.

4. **Condition relations (experimental context) scope.** ~25% of real phenotype annotations carry
   `condition_relations` (anatomy / life-stage / chemical / taxon context via ExperimentalCondition).
   The existing pack does NOT surface these as curatable fields. Keep them out of scope for this
   migration pass (extract statement + subject + term + reference only), or add a
   pending condition-context field now? Recommend out-of-scope for the first builder pass; revisit
   after the core flow is green.

5. **Phenotype term required-ness.** LinkML marks `phenotype_terms` as optional/multivalued and ~30%
   of real DB annotations are **free-text-statement-only with no ontology term**, yet the existing
   pack declares `phenotype_terms[0]` **required** (export-readiness convention). Should the builder
   allow finalizing a phenotype candidate with a free-text statement and **no** ontology term
   (matching DB reality), or keep ≥1 term required? This affects the materializer's per-candidate
   validation and the golden fixture. Recommend allowing term-less candidates (DB-faithful) but
   flagging them blocked-for-export; confirm.

6. **Test document for e2e.** The runbook §7 e2e loop needs a representative processed PDF
   (`DOC=<doc_id>`) that contains curatable phenotype assertions. gene_expression used
   `DOC=a31b1ff3`. No phenotype test doc is recorded yet — Chris/owner should confirm a processed
   document id with real phenotype content before the Phase-3 e2e run.
