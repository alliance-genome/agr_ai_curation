# Ontology Term Resolver — Trigram Fuzzy Search Fix (Plan + Handoff)

Date: 2026-05-30
Status: investigation complete; **API-client trigram PR written, reviewed, and OPEN → [PR #18](https://github.com/alliance-genome/agr_curation_api_client/pull/18).** Next: the `agr_ai_curation` companion changes on `main` (no PR). **This doc is the resume point if context is lost.**

## Where we are (today's session)

- `origin/main` of `agr_ai_curation` is current with three pushed commits from this session:
  - `b5bde024` — Set A: gene-expression builder finalization keyed on source-candidate identity (was pre-existing local WIP).
  - `5c43ce56` — Set B: ".env is the source of truth for model config; remove code fallbacks" (no-fallback refactor, gpt-4o retired/de-registered, model rename gpt-5.4→gpt-5.5 / gpt-5.4-mini→gpt-5-mini / gpt-5.4-nano→gpt-5-nano, compose env wiring).
  - `38bf7949` — completed the gpt-5.4-mini→gpt-5-mini rename + alembic migration `q3r4s5t6u7v8` + single-head pin bump.
- Unit suite is green **CI-style** (`pytest -n 4 --dist loadscope` with the `.ci-ignore-paths`). Serial runs show a pre-existing cross-module test-isolation cascade that xdist hides (not ours). One local-only failure: `test_record_evidence_prompt_contract.py::test_pdf_corpus_trial_examples_do_not_teach_quote_submission` scans a **gitignored** local trial dump (`docs/design/pdf-corpus-trials/main-cross-agent-handoff-20260520-231032/`) — CI never sees it.
- Sandbox is deployed on the new main and healthy (backend `http://192.168.86.44:8900`, frontend `:3900`, TraceReview `:3901`).
- Ran the gene-expression paper PMID39550471 end-to-end on **gpt-5.5**. Trace: `23a1ea9c089a2a866e0dabfd770db45b`.
  - TraceReview `diagnostic_report` works: reasoning summaries **present** (gpt-5.5), 33 tool calls, 6 resolver-ledger entries, validation failure, builder abort.
  - The run hit `MaxTurnsExceeded: Max turns (20)` before `stage`/`finalize`. `AGENT_MAX_TURNS=20` is too low for gpt-5.5's thorough resolver loop → bump to ~40-50 for a full finalize. (Separate from the resolver fix.)

## The problem we are fixing (verified empirically + in code)

The controlled-vocabulary term resolver fails to find terms that clearly exist:
- Agent searched anatomy (WB) for `"ciliated sensory neurons"` → **0 candidates**; for `"AFD neuron"` → **0 candidates**.
- But WBbt (C. elegans anatomy) has **7,191 terms loaded**, including `WBbt:0006816 "ciliated neuron"` and `WBbt:0005662 "AFD"` (+ `AFDL`, `AFDR`).
- GO `cellular_component` resolves *did* succeed (cilium/axoneme) — so the failure is specific to lexical name matching, not data/availability.

**Root cause (code):** The search engine `DatabaseMethods.search_ontology_terms` in `agr-curation-api-client` (`src/agr_curation_api/db_methods.py:1568`) is a **3-tier `UPPER(name) LIKE` search: exact → prefix (`'X%'`) → contains (`'%X%'`)** over `ontologyterm.name` and `synonym.name`. There is **no trigram, no tsvector, no vector**. `pg_trgm` is an unimplemented "Phase 2 TODO" at `db_methods.py:1602`. The resolver's debug `search_channels` list (`trigram_label`, `vector_recall`, …) are **domain-pack config labels, not implemented behavior** — `lookup_methods: []` in the output proves none ran.
- `"ciliated sensory neurons"` → `LIKE '%CILIATED SENSORY NEURONS%'` matches nothing (term is "ciliated neuron").
- `"AFD neuron"` → searches the row `"AFD"` for pattern `%AFD NEURON%` (query is the longer string) → miss. No tokenization.

**Second bug (our repo):** the DB returns a `synonyms` array, but our tool layer **drops it** (`packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py:~3038` and `~3381`) before the model sees it, and the `exact_synonym` scoring path is unreachable dead code. So synonym matches are invisible/un-boosted.

**Verified the fix works:** the curation DB already has `pg_trgm` installed **and** the GIN trigram indexes on exactly the searched columns:
- `ontologyterm_name_trgm_idx ON ontologyterm USING gin (upper(name) gin_trgm_ops)`
- `synonym_name_trgm_idx ON synonym USING gin (upper(name) gin_trgm_ops)`
A direct `word_similarity('ciliated sensory neurons', name)` query returns `ciliated neuron` at **0.60** as the top hit. So **no DB migration is needed — only the query logic.** (The TODO's "add the extension" wording is stale; it's already installed.)

## The plan (the answer this doc captures verbatim)

> Yes — the trigram fix belongs in agr-curation-api-client, and the DB is already prepped for it
>
> What needs updating, split across the two repos:
>
> 1. agr-curation-api-client (the API python program — you can PR this)
> - File/function: src/agr_curation_api/db_methods.py → search_ontology_terms (line ~1568). The pg_trgm TODO is right there at line 1602.
> - What to add: a trigram fuzzy tier alongside the existing exact→prefix→contains tiers. Concretely:
>   - WHERE upper(ot.name) % :query (the pg_trgm % operator) — or word_similarity(:query, upper(ot.name)) >= :threshold — and the same on the synonym join (upper(s.name)),
>   - ORDER BY word_similarity(:query, upper(name)) DESC,
>   - return the similarity score, which field matched (name vs synonym), and the matched text.
> - No DB migration needed. I verified the curation DB already has it: pg_trgm installed + the exact GIN indexes ontologyterm_name_trgm_idx and synonym_name_trgm_idx on upper(name). The % operator and word_similarity use those indexes directly. (The TODO's "add the extension" wording is stale — it's already installed.)
> - I already proved the query works against the live DB: word_similarity('ciliated sensory neurons', name) returns ciliated neuron at 0.60 as the top hit. So this is a small, verified change.
> - Same trigram treatment is worth adding to search_vocabulary_terms (relation CV) and, ideally, a definition search path for assay/method terms.
> - Version note: the checkout on this machine is 0.7.6; production pins 0.10.1. PR against the real repo at 0.10.1 — the structure is identical (the exact/prefix/contains design is confirmed current by a hard-coded limited_search_backend:current_api_exact_prefix_contains warning in our tool code), but grab the exact current line numbers from 0.10.1.
>
> 2. agr_ai_curation (our repo — separate PR)
> The client returns the data, but our tool layer drops it, so two changes here regardless of the client work:
> - Use the new fuzzy search from packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py.
> - Stop dropping synonyms / match metadata — the synonyms array and matched-field info are discarded at agr_curation.py:~3038 and ~3381 before the model sees them, and the exact_synonym scoring path is unreachable dead code. Carry synonyms[] / matched_field / matched_text / score through to the candidate, and score synonym hits as exact. (This is what fixes "AFD neuron" → "AFD", which trigram on the name alone won't catch.)
> - Optional cheap win (could live in either repo): normalize the query — strip generic trailing tokens ("neuron", "cell"), try singular/plural — before searching.
>
> Bottom line: the heavy lift (the actual fuzzy SQL) is one function in agr-curation-api-client, and the DB is already set up for it, so it's a contained PR. Then a small companion PR in our repo to consume it + stop discarding synonyms.

## Execution steps (and Chris's directions)

1. **Clone `agr-curation-api-client` into a temp folder here** (gitignored), check out the `0.10.1` tag/branch (production-pinned), confirm the `search_ontology_terms` exact/prefix/contains structure + TODO at the current line numbers.
2. **Make the API-client PR change:** add the trigram fuzzy tier to `search_ontology_terms` (and ideally `search_vocabulary_terms` + a definition channel), returning `score` + `matched_field` + `matched_text`. Use the existing GIN indexes.
   - Verified SQL pattern: `... WHERE upper(ot.name) % :q ORDER BY word_similarity(:q, upper(ot.name)) DESC` (and the synonym join). Tune threshold via `set_limit()`/`pg_trgm.word_similarity_threshold`.
3. **Sub-agent review (Opus 4.8)** on the API-client change before pushing. (`code-reviewer` / a 4.8 review agent.)
4. **Push + open the PR** on `agr-curation-api-client` (Alliance org; use GITHUB_ALLIANCE_TOKEN).
5. **Then the local `agr_ai_curation` changes — on `main` directly, NO PR** (Chris's instruction): consume the new fuzzy search + stop dropping synonyms (`agr_curation.py:~3038`/`~3381`) + score synonym matches as exact + optional query normalization.
6. (Separately) bump `AGENT_MAX_TURNS` so gpt-5.5 can reach `finalize`, then re-run PMID39550471 to confirm anatomy resolves and the envelope materializes; capture the TraceReview diagnostic.

## Re-orientation facts / commands

- **Curation DB access (readonly):** `CURATION_DB_URL` env in the sandbox backend container (`ai_curation_readonly_curation@host.docker.internal:6331/curation`, tunneled to AWS).
  - Query it: `incus exec symphony-main -- bash -lc 'docker exec -i agrmainsandbox-backend-1 python3 - <<"PYEOF" ... PYEOF'` using `psycopg2.connect(os.environ["CURATION_DB_URL"])`.
- **Ontology tables:** `ontologyterm` (curie, name, namespace, definition, ontologytermtype, obsolete, childcount, descendantcount), `ontologyterm_synonym` + `synonym` (synonym text; exact join column TBD), `ontologytermclosure` (tree/closure), `vocabulary`/`vocabularyterm` (relation CV).
- **Namespaces loaded:** CHEBI, GO, FBbt, UBERON, **WBbt (7191)**, MMO, CL, etc.
- **Resolver tool layer (ours):** `packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py` — `search_domain_field_terms` (~4342), `resolve_domain_field_term` (~4959), `inspect_ontology_term` (~4691), `get_domain_field_term_options` (~4027). Synonyms dropped at ~3038/~3381.
- **API-client (external):** prod pins `agr-curation-api-client==0.10.1` (`backend/requirements.lock.txt`). Local stale checkout `0.7.6` at `/home/ctabone/aws_incident/agr_curation_api_client/`. Engine: `src/agr_curation_api/db_methods.py:search_ontology_terms` (~1568), pg_trgm TODO (~1602).
- **Trace + TraceReview:** trace `23a1ea9c089a2a866e0dabfd770db45b`; `http://192.168.86.44:3901/api/claude/traces/<id>/diagnostic_report?source=local`.
- **Reference materials (saved, gitignored):** `temp/ontology_resolution_refs/` — papers scispaCy (char-3gram TF-IDF + abbreviation), PPR-SSM (graph rerank), Uberon (lexical-is-suggestion-only); cloned repos scispacy, ncbo_annotator, ols4, PPRSSM, ontology-access-kit, uberon. Design docs: `docs/design/2026-05-29-ontology-resolution-reference-research.md`, `docs/design/2026-05-18-alliance-identifier-weaviate-index-design.md`, `docs/design/2026-05-29-gene-expression-linkml-extraction-failure-notes.md`.

## API-client PR — DONE → PR #18 (open)

**Status: written + Opus-reviewed + pushed + PR opened + addressed bot-review round 2.** https://github.com/alliance-genome/agr_curation_api_client/pull/18 (base `main`, head `feat/ontology-trigram-fuzzy-search`). Commits `312ab37` (initial), `ef85aaa` (index-acceleration fix).

### Round 2 — GitHub `@claude` reviewer flagged Tier 4 wasn't using the GIN index (commit `ef85aaa`)
- **Finding (correct):** filtering with the `word_similarity()` **function** form can never use the pg_trgm GIN index — only the trigram **operators** can — so Tier 4 was always seq-scanning, contradicting the "GIN index keeps it fast" docstring.
- **Fix:** WHERE predicates now use the operator `UPPER(col) %> :q` (**single `%>` in source** — SQLAlchemy `text()` auto-doubles `%` for psycopg2; writing `%%>` over-escapes to `%%>` at the DB, a bug I hit live and fixed). Threshold is applied via `pg_trgm.word_similarity_threshold` set **transaction-locally** with `SELECT set_config(..., true)` right before the query (session shares one transaction across tiers since `sessionmaker(autocommit=False)`, so the local GUC persists to the query execute and resets at transaction end — no pooled-connection leak). `word_similarity()` stays only in SELECT/ORDER BY for scoring.
- **Synonym path restructured:** from an `OR`-across-the-join (forces a seq scan of the 312k-row `ontologyterm_synonym` link table) into a **UNION** of two independently index-scannable branches: name-trigram (`ontologyterm_name_trgm_idx`) + synonym-trigram (`synonym_name_trgm_idx`).
- **Live-DB verified:** EXPLAIN shows Bitmap Index Scan on `ontologyterm_name_trgm_idx` (BitmapAnd w/ type index); UNION cost ~21.9k vs OR-join ~43k; operator(GUC=0.3) ≡ function(>=0.3) row sets; UNION ≡ OR-join curie sets; end-to-end `DatabaseMethods` run: typo `"ciliatd neuron"`→`"ciliated neuron"`@0.722, `"sensory ciliated"` exercises the synonym branch (`matched_field='synonym'`). 73 unit tests pass.
- **Residual (DB-schema, outside client):** synonym branch still seq-scans `ontologyterm_synonym` (no usable index on its `synonyms_id`); the synonym trigram index itself IS used. Possible follow-up: add `ontologyterm_synonym(synonyms_id)` index. Name path (common case) is fully index-accelerated.

### Round 2 verdict + Round 3 polish (commit `2098cdc`)
- **GitHub `@claude` re-review APPROVED `ef85aaa`:** "No blockers — this resolves the index-acceleration issue I raised." It independently verified the `%>` commutator parity (`a %> b ≡ word_similarity(b,a) ≥ thr`), the transaction-local GUC, the UNION equivalence, and the tests. CI green (lint/test/database-tests).
- It left **two non-blocking notes**, both addressed in `2098cdc`:
  1. *Escaping only proven live* → added a render-time unit guard: compile the trigram query against the psycopg2 (pyformat) dialect and assert source `%>` → `%%>` and **not** `%%%%>` (catches the single-vs-double-`%` over-escape without a live DB).
  2. *Boundary at exactly threshold* → documented that `%>` matches **strictly greater than** the GUC cutoff (pg_trgm operator semantics; old filter was `>=`), differing only at the exact boundary; framed threshold as a recall knob. No behavior change.
- 73 unit tests pass. Pinged `@claude` again for closure confirmation.

### Version bump + PyPI publish (commit `743f0ee`)
- **Bumped to `0.11.0`** (minor — backward-compatible feature: new tier + new optional `OntologyTermResult` fields). Chris's call (semver minor over a patch).
- Updated `pyproject.toml` (packaging-authoritative) AND re-synced `src/agr_curation_api/__init__.py` `__version__`, which had drifted to `0.9.0` (the `0.9.1`/`0.10.1` bumps only touched pyproject).
- **Build verified:** `python -m build` produces clean `agr_curation_api_client-0.11.0` sdist + wheel.
- **Publish plan:** after PR #18 merges to `main`, **Valerio publishes `0.11.0` to PyPI** (he owns the publish flow). Build artifacts (`*.egg-info`, `dist/`) are gitignored.
- **Downstream:** once `0.11.0` is on PyPI, the `agr_ai_curation` companion change must bump its `agr-curation-api-client` pin to `>=0.11.0` to consume `match_score`/`matched_field`/`match_type` + the trigram fuzzy results.

### Round 3 verdict + final coverage test (commit `b69a871`)
- **GitHub `@claude` re-review APPROVED `2098cdc`:** "Both follow-ups are addressed cleanly. No blockers" — confirmed the escaping guard "has real teeth" and the threshold docs are accurate pg_trgm semantics.
- It left one tiny non-blocking note (its own words: "Not worth a separate test"): the escaping guard only drove the `include_synonyms=True` branch. Chris asked to close it anyway since trivial → added `test_trigram_operator_escaping_in_no_synonyms_branch` (`b69a871`) asserting the non-synonym branch (single-table, no `matched_ids` CTE) also renders `%>`→`%%>` (not `%%%%>`). Test-only; no behavior/version change. **74 unit tests pass.** Pinged `@claude` once more for closure.
- **PR #18 commit stack (final):** `312ab37` (Tier 4) → `ef85aaa` (index fix) → `2098cdc` (review-note polish) → `743f0ee` (v0.11.0) → `b69a871` (non-synonym escaping test). Three `@claude` rounds, all approvals. Ready for human merge → Valerio publishes `0.11.0` to PyPI.

What shipped (vs. the original target below — read for deltas):
- `models.py`: `OntologyTermResult` gained `match_score: Optional[float]`, `matched_field: Optional[str]`, `match_type: Optional[str]` — all default `None` (explicit fields, not just `extra="allow"`), so backward-compatible.
- `db_methods.py`: new `_search_ontology_trigram(session, search_upper, ontology_type, include_synonyms, exclude_curies, limit, threshold=0.3)` mirroring the contains tier's MATERIALIZED CTE + synonym joins + `exclude_curies` tuple-IN. Computes `name_score` and `synonym_score` via `word_similarity()`, reports the higher field. Wired as **Tier 4** in `search_ontology_terms` (runs only when earlier tiers under-fill `limit`; **skipped on `exact_match=True`**). The three existing tiers now set `match_type="exact"/"prefix"/"contains"`. Stale pg_trgm TODO replaced with a Tier-4 docstring.
- **Deltas from plan:** used the **`word_similarity(:q, …)` function form, NOT the `%`/`%%` operator** (avoids psycopg2 escaping + the session-threshold dependency; lets us bind an explicit `threshold`). **Did NOT** touch `search_vocabulary_terms` or add a `definition` channel — kept the PR focused on `search_ontology_terms`; those are follow-ups. No `set_limit()` per-session call needed (threshold is a bound param).
- **Fail-loud, no fallback:** if `pg_trgm` is absent, Tier 4 raises a **self-diagnosing** `AGRAPIError` naming the extension (not a silent degrade).
- **Tests:** `tests/test_ontology_trigram_search.py` (7 mock-based unit tests: tier wiring, `exact_match` short-circuit, `matched_field` selection incl. tie, NULL synonym-score guard, generated-SQL assertion). Full suite: **73 passed, 352 skipped** (DB-integration gated).
- **Live DB validation:** via `scripts/utilities/symphony_curation_db_psql.sh` (readonly tunnel). `pg_trgm` confirmed installed. `"ciliated neuron"` → exact `WBbt:0006816` @ score 1.000 + fuzzy neighbors (SQL valid). Typo `"ciliatd neuron"` → **contains tier 0 hits vs trigram 78 candidates** (the value-add).
- **Auth note:** pushed via `gh` as `christabone` (admin/push on the repo); the separate `GITHUB_ALLIANCE_TOKEN` was not needed.

---

## API-client PR — exact 0.10.1 implementation target (the original plan, kept for reference)

- **Repo cloned to:** `temp/agr_curation_api_client/` (gitignored). Default branch `main` IS `0.10.1` (prod-pinned). **Branch already created: `feat/ontology-trigram-fuzzy-search`.** Remote: `https://github.com/alliance-genome/agr_curation_api_client.git` (Alliance org).
- **Function:** `src/agr_curation_api/db_methods.py` → `search_ontology_terms` at **line 2162**. Tiers are helper methods: `_search_ontology_exact` (2247), `_search_ontology_prefix`, `_search_ontology_contains` (2420). The pg_trgm TODO is at line 2196 (and 1705 for a sibling).
- **Synonym join (correct columns):** `LEFT JOIN ontologyterm_synonym ots ON ot.id = ots.ontologyterm_id LEFT JOIN synonym s ON ots.synonyms_id = s.id`. 0.10.1 ALREADY returns synonyms via `ARRAY_AGG(DISTINCT s.name) ... as synonyms` (line 2282) — so **the client already returns synonyms; only OUR repo drops them.**
- **Return model:** `OntologyTermResult` (`src/agr_curation_api/models.py:332`) = curie, name, namespace, definition, ontology_type, synonyms. It has `model_config = ConfigDict(extra="allow")`, so **add `match_score: float`, `matched_field: str` ("name"|"synonym"), `match_type: str` ("exact"|"prefix"|"contains"|"trigram") as extra fields without a schema break** (and ideally add them explicitly to the model for clarity).
- **Implementation:**
  1. Add `_search_ontology_trigram(session, search_upper, ontology_type, include_synonyms, exclude_curies, limit, threshold)` mirroring `_search_ontology_contains`, but:
     - `WHERE ot.ontologytermtype=:t AND ot.obsolete=false AND (UPPER(ot.name) %% :q OR UPPER(s.name) %% :q)` (the `%` trigram operator — escape as `%%` in SQLAlchemy `text()` if needed, or use `word_similarity(...) >= :threshold`).
     - score: `GREATEST(word_similarity(:q, UPPER(ot.name)), COALESCE(MAX(word_similarity(:q, UPPER(s.name))), 0))` (synonym needs aggregation in the GROUP BY CTE like the existing exact query).
     - `ORDER BY match_score DESC` (relevance, unlike the existing alphabetical `ORDER BY name`).
     - Set a tunable threshold via param (default ~0.3) or `SELECT set_limit(:threshold)` per-session; uses the existing GIN indexes `ontologyterm_name_trgm_idx` / `synonym_name_trgm_idx` (verified present).
     - Populate `match_score`, `matched_field`, `match_type="trigram"` on each `OntologyTermResult`.
  2. Wire into `search_ontology_terms` as **Tier 4** after contains when `len(results) < limit` (or behind a `fuzzy: bool = True` param). Keep exact/prefix first so precise hits still win; trigram fills the gap.
  3. Update the docstring (remove the stale "add pg_trgm extension" TODO — extension+indexes already exist; note trigram tier is implemented).
  4. Also add the same trigram path to `search_vocabulary_terms` (relation CV in `vocabularyterm`) and, ideally, a `definition` search channel for assay/method terms.
- **Verified SQL** (run against live DB, found `ciliated neuron` @ 0.60 for `'ciliated sensory neurons'`):
  `SELECT curie,name, word_similarity('ciliated sensory neurons', name) ws FROM ontologyterm WHERE curie LIKE 'WBbt:%' AND obsolete=false ORDER BY ws DESC LIMIT 6;`
- **Tests:** add unit tests in the client repo for the trigram tier (mock/real DB) with fixtures `mechanosensory neurons`→`mechanosensory neuron`, `ciliated sensory neurons`→`ciliated neuron`, `AFD neuron`→`AFD` (via synonym), and a threshold/no-match case.
- **Then:** Opus 4.8 sub-agent code review on the diff → push branch → open PR on `alliance-genome/agr_curation_api_client` (needs Alliance token; repo cloned anonymously so push needs auth).

## agr_ai_curation companion changes (on `main`, NO PR — Chris's call)
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py`: stop dropping `synonyms` at `~3038` (anatomy branch maps only `{curie,name,ontology_type}`) and `~3381` (`_ontology_helper_result` builds candidate without synonyms); carry `synonyms[] / matched_field / matched_text / match_score` into the candidate (`_resolver_candidate_from_helper_result` ~3530) and make synonym matches score 1.0 (the `exact_synonym` path is currently unreachable). Then consume the new trigram results.
- Optional query normalization (strip "neuron"/"cell" suffix, singular/plural) — can live here.
- Bump `AGENT_MAX_TURNS` (~40-50) so gpt-5.5 reaches `finalize`; re-run PMID39550471 + capture TraceReview.

## Decisions locked
- **Weaviate: not now, not the authority.** pg_trgm fuzzy first (no migration). Weaviate `AllianceOntologyTerm` recall layer is a later, prototype-gated, recall-only enhancement.
- **API-client change → PR** (Alliance repo, with Opus 4.8 review). **agr_ai_curation change → directly on `main`, no PR.**
- The DB already has pg_trgm + trigram GIN indexes → the API-client change is pure query logic.
