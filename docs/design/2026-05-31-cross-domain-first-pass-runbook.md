# Cross-Domain Builder Migration — Multi-Agent Guide (LIVING DOC)

Date: 2026-05-31. For a detached multi-agent workflow doing a FIRST REAL CODE PASS that migrates
each envelope-pattern data type to the gene_expression builder pattern. gene_expression is the
proven, structurally-clean reference. First pass — we iterate later; getting boots on the ground
is the point, but the invariants in §5 and the safety/test/review gates in §6–§8 are mandatory.

Companion: `2026-05-31-builder-inline-validation-and-cross-domain-migration.md` (architecture +
9-step sequence + "what NOT to touch"). Read its §"What NOT to touch" before any change.

> SUPERSEDES the earlier docs-only scope of this file. This is now a CODE-migration guide.

---

## 0. How to use this doc (every agent reads this first)

1. This is a LIVING doc. **Claim a data type** in the Status Table (§10) by editing this file
   (set Owner + status `in_progress`), commit that one-line change first so others see it.
2. Follow the **recipe (§4)** for your type, grounded in **LinkML + the curation DB (§3)**.
3. **COPY gene_expression** (§2). Do not invent shapes; mirror the reference.
4. Obey the **invariants (§5)** — they are today's hard-won bug fixes; violating them re-introduces
   known bugs.
5. After any code change: **git safety (§6) → sandbox deploy+test (§7) → Opus 4.8 code review (§8)**.
6. Update the Status Table + Progress Log (§10/§11) as you go. One commit per meaningful step.
7. **Stop and write an `## Open questions` entry** (in your type's approach notes) instead of
   guessing on any genuine design decision (ambiguous LinkML, conflicting curation reality, a
   required slot with no extraction source). Leave it for Chris; keep moving on the rest.

---

## 1. Mission & scope

Migrate `gene` (gene_extractor), `disease`, `phenotype`, `allele`, `chemical_condition` from the
**envelope pattern** (agent has `output_schema`, one-shot structured output) to the **builder
pattern** (agent stages candidates via tools, then a `finalize_<type>_extraction` tool materializes
the envelope; inline validation runs in the chat turn). Target end state per type: a fresh sandbox
extraction persists a structurally-clean envelope — zero `entity_assayed_mismatch`,
`object_not_pending`, `metadata_refs_missing`, `validator_materialization_invalid`; only
`validator_resolved` (INFO) and genuine `validator_unresolved`/`validator_error` remain.

DO NOT delete envelope-legacy machinery in this pass (that is the final phase, after ALL types are
builders and green — see companion doc step 7). Add builder paths alongside; leave envelope code.

---

## 1A. Phase plan (the backbone — strictly in order)

The migration runs as explicit phases. **gene_expression is the source-of-truth example** every
phase copies for structure (§2); every phase is grounded in the **LinkML model** (§3) and ENDS
with a mandatory **Opus 4.8 code review (§8)** + green sandbox e2e (§7) before its commit lands.
**Do not start any per-type phase until Phase 0 is merged.**

- **Phase 0 — Make the builder tools/engine GENERIC (prerequisite; blocks all per-type phases).**
  So per-type phases add thin domain adapters, not platform edits:
  1. Generalize builder detection — replace the hardcoded
     `_BUILDER_MATERIALIZER_FINALIZATION_TOOLS` / `_is_builder_materializer_agent`
     (`streaming_tools.py:547/554`) with a domain-pack/registry-derived set of finalize-tool
     names; keep the forbid-output-schema guard (~2366).
  2. Factor the generic staging/finalize tool surface over `ExtractionBuilderWorkspace` into a
     reusable **per-domain builder-tools module** pattern (thin adapter), pulling shared logic out
     of `_finalize_gene_expression_extraction_impl`.
  3. **Refactor gene_expression onto the generic tools as the proof/canary** — it must stay
     structurally clean after the refactor (ge17 baseline: 0 structural findings). This makes
     gene_expression both the source-of-truth example AND Phase 0's regression guard.
  - Gate: gene_expression unit/contract + e2e still green; Opus 4.8 review of the Phase-0 diff.

- **Phase 1 — gene (canary type):** first real per-type migration; proves the generic infra on a
  fresh type. Ground in `gene.yaml` + curation DB `gene`.
- **Phase 2 — disease:** `phenotypeAndDiseaseAnnotation.yaml` (`*DiseaseAnnotation`) + curation DB
  `genediseaseannotation` / `diseaseannotation_*`.
- **Phase 3 — phenotype:** `*PhenotypeAnnotation` + curation DB phenotype tables.
- **Phase 4 — allele:** `allele.yaml` (`Allele`) + curation DB `allele*`.
- **Phase 5 — chemical_condition:** `ExperimentalCondition` + chemical CV + the existing pack.
- **Phase 6 — (LATER, NOT this pass) delete envelope legacy** once ALL types are builders and
  green (companion doc step 7+). Out of scope now.

Per-phase gate (all required before the phase's commit lands): LinkML-grounded approach doc →
gene_expression-shaped implementation → §7 sandbox unit + e2e green (0 structural findings) → §8
Opus 4.8 review clean → §6 git-safe commit → Status Table (§10) updated.

---

## 2. Reference anatomy — the gene_expression files to copy

| Concern | File | Notes |
|---|---|---|
| Agent def | `packages/alliance/agents/gene_expression/agent.yaml` | NO `output_schema`; builder tool list (stage/patch/discard/list/finalize + evidence + selector-resolution tools) |
| Prompt | `packages/alliance/agents/gene_expression/prompt.yaml` | builder tool-loop instructions |
| Group rules | `packages/alliance/agents/gene_expression/group_rules/{wb,zfin}.yaml` | per-MOD overrides (optional) |
| Builder tools + finalize impl | `packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py` | `_finalize_gene_expression_extraction_impl` (~5957), candidate helpers (~5354), event emitters (~5412) |
| Tool registration | `packages/alliance/tools/bindings.yaml` | wires tool names → impls |
| Per-domain materializer | `packages/alliance/python/.../domain_packs/gene_expression/conversion.py` | `materialize_gene_expression_builder_state` (builder state → extraction-output payload), `_metadata_ref_findings`, projection/contract checks |
| Domain pack metadata | `packages/alliance/domain_packs/gene_expression/domain_pack.yaml` | objects, fields, validator bindings, `materializes_to_field_paths`, workspace_display |
| Golden fixtures | `packages/alliance/domain_packs/gene_expression/fixtures/tmem67_pending.yaml` | expected pending envelope (RELATIVE metadata_refs) |
| Builder detection | `backend/src/lib/openai_agents/streaming_tools.py:547` `_BUILDER_MATERIALIZER_FINALIZATION_TOOLS` + `_is_builder_materializer_agent` (554); forbid-output-schema guard (~2366) | generalize in Phase 0 |
| Shared engine | `backend/src/lib/openai_agents/extraction_builder_workspace.py` `ExtractionBuilderWorkspace` | generic stage/finalize/discard — REUSE, do not clone |
| Curation resolution for builders | `backend/src/lib/agent_studio/catalog_service.py` `get_agent_metadata` (~2236) | already follows template_source to inherit curation |

Contract: the agent emits an `INTERNAL_EXTRACTION_RESULT` whose payload is the extraction-output
shape `{summary, curatable_objects, metadata: ExtractionEnvelopeMetadata}`. The generic converter
`_domain_envelope_from_extraction_result` (curation_prep_service.py) turns it into a DomainEnvelope,
nesting `metadata` under `metadata.extraction_metadata` and stamping platform keys at top level.

---

## 3. Grounding per data type (no Daniela doc — derive it)

For each type, ground the design in three real sources before writing code:

**LinkML** (`temp_agr_curation_schema/model/schema/`, read-only clone @ 1b11d088):
- gene → `gene.yaml` (`Gene`), identity in `core.yaml`.
- disease → `phenotypeAndDiseaseAnnotation.yaml` (`*DiseaseAnnotation`).
- phenotype → `phenotypeAndDiseaseAnnotation.yaml` (`*PhenotypeAnnotation`).
- allele → `allele.yaml` (`Allele`).
- chemical_condition → `core.yaml` `ExperimentalCondition` + chemical CV (ChEBI/ZECO); confirm
  from the existing `chemical_condition` pack.
- reference: gene_expression → `expression.yaml` (already done).

**AWS curation DB** (readonly, reachable from the backend container; 189 tables). Read 5–20 real
curated rows per type to learn what curators actually fill, the real CURIE namespaces, reliably-
present slots. SELECT-only, light queries, never print the URL:
```
incus exec symphony-main -- bash -lc 'docker exec agrmainsandbox-backend-1 bash -lc '"'"'python3 -c "
import os, psycopg2
c=psycopg2.connect(os.environ[\"CURATION_DB_URL\"]); cur=c.cursor()
cur.execute(\"select table_name from information_schema.tables where table_schema=%s and table_name ilike %s order by 1\", (\"public\",\"%disease%\"))
print([r[0] for r in cur.fetchall()]); c.close()"'"'"''
```
Discovered tables include `gene`, `genediseaseannotation`, `diseaseannotation_*`, `allele*`,
`geneexpressionannotation`. Literature DB (`LITERATURE_DB_URL`) tunnel may be DOWN — note + skip;
`ELASTICSEARCH` `references_index` is the PMID search index.

**Existing pack** (`packages/alliance/domain_packs/<type>/domain_pack.yaml` + any
`.../domain_packs/<type>/conversion.py`): the current envelope objects, fields, and validator
bindings — reuse the bindings; you're changing the extraction mechanism, not the curation target.

Capture the grounding for your type in `docs/design/data-type-approaches/<type>-approach.md`
(target class+slots → curation-DB reality → curatable objects/fields → validators → evidence →
builder mapping → open questions). Commit it; it justifies the code.

---

## 4. Per-type migration recipe (copy gene_expression, file by file)

1. **Approach doc** (§3): write + commit `docs/design/data-type-approaches/<type>-approach.md`.
2. **Per-domain materializer**: add `materialize_<type>_builder_state` in
   `packages/alliance/python/.../domain_packs/<type>/conversion.py`, mirroring
   `materialize_gene_expression_builder_state`. It reads the builder workspace candidates and emits
   the extraction-output payload (`curatable_objects` + `metadata` with RELATIVE `metadata_refs`).
3. **Builder tools**: add `stage_<type>_observation`, `patch_*`, `discard_*`,
   `list_staged_*`, and `finalize_<type>_extraction` (calls the materializer) — mirror
   `_finalize_gene_expression_extraction_impl`. PREFER a per-domain module over piling into
   `agr_curation.py` (avoids the shared-file conflict; see §9). Register in `bindings.yaml`.
4. **Agent**: in `packages/alliance/agents/<type>/agent.yaml` REMOVE `output_schema`; swap the tool
   list to the builder set (evidence + selector-resolution + the new stage/finalize tools). Rewrite
   `prompt.yaml` into a builder tool-loop (record evidence → stage observations → resolve selectors
   → finalize), copying gene_expression's prompt structure and adapting the domain specifics.
5. **Domain pack metadata** (`domain_pack.yaml`): ensure object definitions, fields,
   `validatable`/`validator_binding_id`, and any `materializes_to_field_paths` mirrors (LinkML
   "X must match Y") are declared. Reuse existing validator bindings where present.
6. **Detection**: register `finalize_<type>_extraction` so `_is_builder_materializer_agent` sees
   it. PREFER the Phase-0 generalized (domain-pack-derived) detection over editing the hardcoded
   frozenset (§9).
7. **Fixtures + tests**: add a golden pending fixture (RELATIVE metadata_refs) and unit/contract
   tests mirroring `test_gene_expression_domain_pack.py`. Run them (§7).
8. **Sandbox e2e** (§7): drive a real extraction; confirm structural findings are 0.
9. **Code review (§8)** → fix → commit (§6) → update Status Table.

---

## 5. Invariants (today's bug fixes — NON-NEGOTIABLE)

1. **`metadata_refs` are RELATIVE** to the extraction-metadata namespace (`raw_mentions[N]`,
   `evidence_records[N]`, `ambiguities[N]`, …) and resolved against
   `envelope.metadata.extraction_metadata`. NEVER write absolute `extraction_metadata.<path>` refs
   and NEVER rewrite refs in a converter. (commit 98a9b3d3)
2. **Object status**: `PENDING` = "not yet validated by the automated validator". Validation
   legitimately advances a resolved object to `VALIDATED`; this is unrelated to curator review. Do
   NOT add an "objects must be pending" check. (commit 91f7a784)
3. **Validator errors are NON-FATAL**: a validator that cannot run → distinct
   `domain_pack.validator_error` OPEN finding (vs `validator_unresolved` = ran/no-match); the
   extraction still persists. Never let a validator error abort the chat turn. (commit abfe55ed)
4. **Mirror fields via declared `materializes_to_field_paths`** metadata, not code special-casing
   (gene_expression: subject gene → `entity_assayed`). (commit 2ec6b3b9)
5. **Inline validation happens in the chat turn** on the builder-finalized envelope (extraction →
   validation → reply), and the validated envelope + findings persist so bootstrap reuses them.
   Builder output DOES run validators (do not skip). (Parts 1–3, design doc)
6. **Project-agnostic core**: domain-specific behavior lives in the domain pack / per-domain
   adapter / config — never hardcoded in `backend/src` platform services. **No fallback/compat
   shims** (forward-only).

---

## 6. Git safety (MANDATORY)

- Repo: `alliance-genome/agr_ai_curation`, branch `main` (agr_ai_curation lands on main directly,
  no PR). Verify before every commit: `git rev-parse --show-toplevel` (this repo) and
  `git remote get-url origin` (the alliance remote).
- `/secure-repo` git hooks (gitleaks + TruffleHog + parent-dir protection) are installed and
  **must never be bypassed**. NEVER `--no-verify`. If a hook errors, STOP and surface it; do not
  work around it.
- Stage with **explicit file paths only** — never `git add -A`/`git add .`. Confirm
  `git diff --cached --name-only` is exactly your intended files before committing.
- Pull/rebase `main` immediately before committing (parallel agents are merging).
- Never print secrets / DB URLs.

---

## 7. Sandbox deploy + test (the proven loop)

Sandbox = Incus VM `symphony-main`, compose project `agrmainsandbox`, backend `127.0.0.1:8900`
(inside VM), worktree `WT=/home/ctabone/.symphony/sandboxes/agr_ai_curation/main` with uvicorn
`--reload`. Backend mounts `backend/src`, `packages`, `backend/tests` (tests read-only) from WT.

**Deploy a changed file** (uvicorn reloads on any `backend/src` change, re-importing packages too):
```
incus file push <relpath> symphony-main$WT/<relpath> --uid 1000 --gid 1000
```
**Run unit/contract tests** (push test files first; tests dir is mounted read-only from WT):
```
incus exec symphony-main -- bash -lc 'docker exec -w /app/backend agrmainsandbox-backend-1 \
  python -m pytest tests/contract/alliance/domain_packs/test_<type>_domain_pack.py -q'
```
**End-to-end** (one real extraction → findings). Harness pattern (adapt SID/DOC per type; each
type needs a representative processed PDF — gene_expression used `DOC=a31b1ff3`; find/confirm a
test doc per type and record it in the approach doc):
```
BASE=http://127.0.0.1:8900; DOC=<doc_id>; SID=<type>-testNN-$(date +%s)
curl -s -X POST $BASE/api/chat/document/load -d "{\"document_id\":\"$DOC\"}" -H 'Content-Type: application/json'
curl -s -m600 -X POST $BASE/api/chat -d "{\"message\":\"Extract all <type> from this publication\",\"session_id\":\"$SID\"}" -H 'Content-Type: application/json'
curl -s -m300 -X POST "$BASE/api/curation-workspace/documents/$DOC/bootstrap" -d "{\"origin_session_id\":\"$SID\"}" -H 'Content-Type: application/json'
```
**Findings by code** (confirm structural findings are 0) — `/tmp/findings_by_code.sql` queries the
latest envelope's revision, grouped by `code`, against `agrmainsandbox-postgres-1`:
```
incus exec symphony-main -- bash -lc 'docker exec -i agrmainsandbox-postgres-1 \
  psql -U postgres -d ai_curation -At -F"  " < /tmp/findings_by_code.sql'
```
PASS = no `entity_assayed_mismatch` / `object_not_pending` / `metadata_refs_missing` /
`validator_materialization_invalid`; only `validator_resolved`/`validator_unresolved`/(maybe)
`validator_error`. Builder workspace can be "finalized" if a doc was already extracted — use a
fresh SID or a fresh document.

---

## 8. Code review with Opus 4.8 (after every major code addition)

After a type's materializer + builder tools + agent are in and unit-green, BEFORE final commit,
run a review with Opus 4.8. Spawn the `code-review` agent (or a general agent, model `opus`) on
the diff:
```
Agent(subagent_type="code-review", model="opus",
  prompt="Review the <type> builder-migration diff vs main. Check: invariants in
  docs/design/2026-05-31-cross-domain-first-pass-runbook.md §5 (metadata_refs relative; status
  semantics; non-fatal validator errors; materializes_to_field_paths; project-agnostic, no
  fallbacks); parity with the gene_expression reference; debug code / leftover scaffolding;
  test coverage. Report only high-confidence issues with file:line.")
```
Address blockers before committing. Record the review verdict in the Status Table.

---

## 9. Multi-agent coordination

**Phase 0 (ONE agent, FIRST, lands on main before any per-type work):**
- Generalize builder detection: replace the hardcoded
  `_BUILDER_MATERIALIZER_FINALIZATION_TOOLS` / `_is_builder_materializer_agent`
  (`streaming_tools.py:547/554`) with a domain-pack/registry-derived set of finalize-tool names
  (keep the forbid-output-schema guard ~2366). This removes the only forced per-type edit to a
  shared platform file.
- Establish the **per-domain builder-tools module** pattern (thin adapter over
  `ExtractionBuilderWorkspace`) so each type adds its tools in its OWN module, not the monolithic
  `agr_curation.py`. Factor shared helpers from `_finalize_gene_expression_extraction_impl`.
- Unit-test + commit Phase 0. Only then unblock per-type agents.

**Per-type agents (parallel after Phase 0):** each OWNS a disjoint file set — its
`agents/<type>/`, `domain_packs/<type>/conversion.py`, `domain_pack.yaml`, fixtures, per-type
tests, and its per-domain builder-tools module. No two agents edit the same file. If you must
touch a shared file, rebase `main` first, make the minimal append, commit immediately, re-rebase.
Use worktree isolation if the workflow provides it.

**Order:** gene (canary, simplest) → disease → phenotype → allele → chemical_condition.

**Definition of done per type:** approach doc committed; materializer + builder tools + agent in;
unit/contract tests green in sandbox; e2e extraction shows 0 structural findings; Opus 4.8 review
clean (or issues resolved); Status Table updated; committed + pushed to main.

---

## 10. Status table (claim your type here — edit + commit first)

| Phase | Type | Owner | Approach doc | Code | Unit | E2E | Opus review | Status |
|---|---|---|---|---|---|---|---|---|
| 0 | generic builder infra + gene_expression refactor (canary) | Claude | n/a | done | 84/84 | 0 struct | clean | DONE (7d891dbe) |
| pre | validator_materialization_invalid fix (baseline cleanup) | Claude | n/a | 16/16 | 8→0 | clean | DONE (eb59c04e) | (made the 0-struct gate genuine) |
| 1 | gene | Claude | done | done | 172+8 | 0 struct | clean | DONE (39663f46) |
| 2 | disease | Claude | done | ☐ | ☐ | ☐ | ☐ | grounded; impl pending open-Qs + test PDF |
| 3 | phenotype | Claude | done | done | 10 new | 33 units, 0 struct | clean | DONE (b42cdea1) |
| 4 | allele | Claude | done | done | 10 new | 6 assoc, 0 struct | clean | DONE (eca78ad8) |
| 5 | chemical_condition | Claude | done | ☐ | ☐ | ☐ | ☐ | grounded; impl pending open-Qs + test PDF |

(gene_expression = reference, already done + structurally clean as of ge17.)

---

## 11. Progress log (append; newest last)

- 2026-05-31: Guide created. gene_expression reference is structurally clean (ge17: 0 structural
  findings). Curation DB readonly confirmed (189 tables); LinkML clone present; literature DB
  tunnel down. Builder anatomy mapped (§2). Awaiting Chris's go to launch the workflow.
- 2026-05-31 (afternoon, autonomous run): FOUNDATION LANDED ON MAIN.
  - Sandbox worktree had diverged (stale base 7e343619 + 21 dirty files == main content); reconciled
    to clean main with zero progress lost (all 20 tracked dirty files were byte-identical to main).
  - Prerequisite bug fix `validator_materialization_invalid` (commit eb59c04e): the runbook's
    "ge17 = 0 structural findings" baseline was actually inaccurate — a fresh baseline run on
    unmodified main reproduced validator_materialization_invalid. Root cause: scalar validator
    bindings (e.g. subject_gene_validation) report their raw lookup hit in `resolved_objects` as
    DIAGNOSTIC context (object_type + resolved_id/provider_data, no canonical_id/payload), but
    `_looks_like_materializable_object` treated any object with an object_type key as a
    materialization candidate, tripping the strict `canonical_id is required` check. Fix: identify
    materialization payloads by canonical_id/payload (not object_type); diagnostic projections are
    skipped, genuine validated_reference payloads still fully validated. gene_expression e2e:
    validator_materialization_invalid 8 → 0. Unit RED→GREEN (16/16), Opus 4.8 review CLEAN. This
    makes the "0 structural findings" gate GENUINE for all per-type phases.
  - Phase 0 (commit 7d891dbe): builder detection generalized — `_BUILDER_MATERIALIZER_FINALIZATION_TOOLS`
    frozenset replaced by `_builder_finalization_tool_names()` deriving the finalize-tool set from a
    `builder_finalization: true` tool-binding metadata flag (forbid-output-schema guard preserved);
    new domain-agnostic `builder_finalization.py` finalize orchestration over ExtractionBuilderWorkspace;
    `_finalize_gene_expression_extraction_impl` refactored into a thin adapter delegating to
    `finalize_builder_extraction(materialize=_materialize_gene_expression_with_events, ...)`.
    gene_expression canary: 84/84 unit + e2e 0 structural findings (no regression). Opus 4.8 review
    CLEAN. Non-blocking follow-ups (iterate later): the `gene_expression_materializer.completed`
    trace event no longer carries `materialized_candidate_id` (observability only); error-path
    message strings now prefixed with the tool name (no test asserts them).
  - PER-TYPE TEMPLATE for phases 1-5: copy gene_expression — add a `materialize_<type>_builder_state`
    in `domain_packs/<type>/conversion.py`; a thin finalize adapter that calls
    `finalize_builder_extraction(...)` with that materializer + `materialized_candidate_prefix`;
    register the finalize/stage tools in bindings.yaml WITH `metadata.builder_finalization: true` on
    the finalize tool; agent.yaml drops output_schema + uses the builder tool list; prompt.yaml is a
    builder tool-loop; domain_pack.yaml declares objects/fields/validator bindings. NO platform edits.
  - Sandbox reconciled to main (7d891dbe). Phases 1-5 unblocked; starting gene (Phase 1).
- 2026-05-31 (afternoon cont.): PHASE 1 (gene) LANDED (commit 39663f46) + all 5 types grounded.
  - gene migrated envelope→builder, mirroring gene_expression. Target is a gene_mention_evidence
    validated-reference object; the gene VALIDATOR owns identity, so gene has NO resolver-backed
    controlled fields and NO mirror fields (require_resolver_selections=False). Added per-domain
    materialize_gene_builder_state (RELATIVE metadata_refs), a per-domain gene_builder_tools.py over
    the generic finalize_builder_extraction, bindings.yaml registration (builder_finalization +
    builder_run_state flags), agent.yaml output_schema dropped, builder-loop prompt, golden fixture,
    contract test. E2E on a31b1ff3: routed to gene_extractor, finalize_gene_extraction called,
    inline validation dispatched 11 bindings IN THE CHAT TURN, persisted envelope (domain_pack=gene,
    11 gene_mention_evidence objects) = 0 structural findings (validator_resolved x11 only). 172+8+25
    tests pass. Opus 4.8 review CLEAN.
  - SECOND platform generalization (needed + made, reviewed clean): _RUN_STATE_TOOL_IMPLS in
    streaming_tools.py was still a hardcoded per-type map (Phase 0 generalized only detection +
    finalize). gene made it registry-derived from a new `builder_run_state: true` binding-metadata
    flag — same pattern as Phase 0's builder_finalization, NO per-type names in backend/src. The
    platform is now FULLY GENERIC for builder types: a new type needs only domain-pack/adapter files
    + the two binding-metadata flags (builder_finalization on finalize, builder_run_state on staging
    tools). NO platform edits for phases 2-5.
  - Template watch-items for phases 2-5 (from gene's review): the evidence-locator contract
    hard-requires payload section+chunk_id though the source EvidenceRecord has them Optional —
    confirm each type's evidence tool always populates them; and trim verbose materializer trace
    payloads.
  - Phases 2-5 (disease/phenotype/allele/chemical_condition): GROUNDED — approach docs written under
    docs/design/data-type-approaches/ from LinkML + the live curation DB + the existing envelope
    packs. Each surfaced genuine OPEN QUESTIONS FOR CHRIS (see each doc's "Open questions" section):
    mostly whether the builder preserves the existing pack's conservative posture (write-blocked /
    mention-only / deferred fields) or expands scope, plus ontology/validator coverage gaps. Per §3
    ("change the mechanism, not the curation target") the default is preserve-existing-posture, but
    several are real scope decisions. BLOCKER for all four e2e gates: the sandbox has only ONE
    processed PDF (a31b1ff3, gene-expression) — no representative disease/phenotype/allele/chemical
    test document. Implementation of phases 2-5 awaits Chris's open-question decisions + test PDFs;
    structural correctness is otherwise coverable via gene_expression-style unit/contract fixtures.
- 2026-05-31 (afternoon cont.): PHASE 3 (phenotype) LANDED (commit b42cdea1). Migrated phenotype
  envelope→builder mirroring gene/gene_expression: per-domain materialize_phenotype_builder_state
  (one PhenotypeAnnotation curatable_unit + pending PhenotypeSubject/PhenotypeTerm/Reference/
  EvidenceQuote sub-objects, RELATIVE metadata_refs), phenotype_builder_tools.py over the generic
  finalize_builder_extraction, bindings flags (builder_finalization + builder_run_state), agent
  output_schema dropped, builder-loop prompt, golden fixture, contract test (10 new; 157 suite pass).
  NO backend/src edits. E2E on a31b1ff3: routed to phenotype_extractor, 33 PhenotypeAnnotation units
  materialized (172 objects), inline validation dispatched 33 bindings in the chat turn, 0 structural
  findings (validator_unresolved x33 = expected pending posture for free-text labels the active
  WB/MGI ontology validator doesn't resolve). Opus review CLEAN. ALL 6 approach-doc open questions
  resolved as PRESERVE-EXISTING-POSTURE (did NOT activate new ontology/provider pairs, did NOT expand
  scope) — the namespace-coverage + term-required-ness decisions remain open for Chris. Phenotype
  also addressed the gene-review watch-item (treats EvidenceRecord section/chunk_id as Optional).
  STATUS: phases done = pre(validator fix), 0, 1(gene), 3(phenotype). Remaining: allele (attempting;
  e2e feasibility on a31b1ff3 uncertain), disease + chemical_condition (no representative test PDF in
  sandbox → grounded-only, awaiting Chris's open-Q decisions + test data; will not commit
  prompt-unverified builders for types that can't be exercised e2e).
- 2026-05-31 (afternoon cont.): PHASE 4 (allele) LANDED (commit eca78ad8). Migrated allele
  envelope→builder mirroring gene (mention-only) + phenotype (multi-object graph). agents/allele_extractor
  migrated; agents/allele (the validator) left intact. Stays mention-only (require_resolver_selections
  =False; allele_mention_reference_validation owns identity). New materialize_allele_builder_state +
  allele_builder_tools.py + conversion.py + bindings flags + builder agent/prompt + golden fixture +
  contract test (10 new; 167 suite pass). NO backend/src edits. E2E on a31b1ff3: routed to
  allele_extractor, 6 AllelePaperEvidenceAssociation candidates (29 objects), inline validation in the
  chat turn resolved gcy-9(tm2816)/pef-1(gk5346)/osm-3(p802)/che-3(e1124) to WBVar curies, 0 of the 4
  structural codes. The 19 required_field_missing + write_blocked findings are PRE-EXISTING pack posture
  (verified: the existing envelope converter produces identical findings) — faithfully reproduced, not a
  regression. Opus review CLEAN. Open questions preserved-as-existing-posture (mention-only; not
  capturing mutation-type SO terms).
- 2026-05-31 SESSION CLOSE (autonomous run): DONE + on main = pre(validator_materialization_invalid
  fix eb59c04e), Phase 0 (7d891dbe), Phase 1 gene (39663f46), Phase 3 phenotype (b42cdea1), Phase 4
  allele (eca78ad8), + runbook/grounding commits. 4 of 6 envelope extractors now on the builder pattern
  (gene_expression reference + gene + phenotype + allele); the builder runtime is fully generic
  (builder_finalization + builder_run_state metadata flags; zero per-type platform edits after gene).
  REMAINING for Chris:
  * Phase 2 (disease) + Phase 5 (chemical_condition): GROUNDED ONLY (approach docs committed). NOT
    implemented because (a) the sandbox has NO representative test PDF (only a31b1ff3, a gene-expression
    paper with no disease annotations or formal experimental/chemical conditions) so a meaningful e2e is
    impossible, and (b) each carries real scope open questions (disease: write-blocked posture, ECO
    codes, relation-vocab subset; chemical: WBMol coverage, relation types beyond has_condition). Per the
    playbook we do NOT guess on genuine design decisions and do NOT commit prompt-unverified builders.
    To finish them: answer the open questions in their approach docs + stage one disease and one
    chemical-bearing PDF in the sandbox, then run the same per-type workflow (the gene/phenotype/allele
    pattern is proven and copy-paste-able).
  * Open questions across all four grounded types are consolidated in each docs/design/data-type-approaches/
    <type>-approach.md "Open questions" section.
  * Minor follow-ups (non-blocking): trim verbose materializer trace payloads; the per-type builder-tools
    Optional-access (BuilderFinalizationOutcome.finalization) + str|None Pyright nits are type-checker
    strictness only (all tests + e2e green) but could be tidied.
