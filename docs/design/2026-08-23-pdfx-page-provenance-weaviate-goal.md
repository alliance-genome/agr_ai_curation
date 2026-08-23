# PDFX Page Provenance to Weaviate Goal

**Date:** 2026-08-23

**Status:** Active

**Planning/forward-port branch:** `fix/pdfx-page-provenance-consumer-20260823`

**Production hotfix branch:** `hotfix/v0.8.21-weaviate-page-provenance`

**Exact production base:** tag `v0.8.20` at
`6df184c6fc48616a56cc7a6828e0bcd981cb2894`

**Current main:** `416c62aa735cf2545db45ec9f8013c421fdd8e98`
(AI Curation PR #628) when this goal began

**Official goal:** Make AI Curation consume PDFX's production-proven,
digest-bound `page_provenance` sidecar for local merged-PDFX ingestion, carry
the resulting primary page numbers through existing chunks into Weaviate, and
prove the behavior with a fresh dev extraction. After the implementation PR,
reviews, automated gates, and dev evidence pass, prepare and execute a normal
tagged AI Curation production release and verify the page-aware path in
production.

**Production authorization:** On 2026-08-23 Chris explicitly authorized the
full production release. This authorizes the normal release-runbook mutations;
it does not waive a failed test/review/dev gate, maintenance-first production
safety, backups, or rollback readiness. A newly discovered material user-data
or migration risk must still be surfaced before production mutation.

This document is the single source of truth and resume point for the goal. The
ignored historical workpad `temp/WEAVIATE_DEV_BENCHMARK_GOAL.md` and the local
deployment report
`~/.agr_ai_curation/docs/deployments/2026-08-21_DEV_WEAVIATE_BENCHMARK_REPORT.md`
remain evidence only. Their recommendation to deploy PDFX commit `05687ea`
with inline page comments is superseded by the sidecar contract described
below.

Production is intentionally behind current `main`. This release must not deploy
`main` or accidentally include its intervening application work. Build and
prove a narrow hotfix from exact live tag `v0.8.20`, port the already-reviewed
Weaviate PR #628 change onto that baseline, add the page-sidecar consumer, and
release that bounded result. After it is proven, forward-port the page consumer
and release-state updates to current `main` in a separate PR; do not replay PR
#628 there because `main` already contains it.

## 1. Completed upstream foundation

The retrieval benchmark and PDFX producer work are already complete. Do not
repeat them unless a concrete new defect invalidates their evidence.

- AI Curation PR #628 merged the evidence-based Weaviate retrieval defaults
  and native backup support as `416c62aa`.
- PDFX PR #48 merged and deployed the final sidecar producer as
  `94dd55556235079a8b5ddac5ce49c5374678d766`.
- `agr-abc-document-parsers` 1.7.2 is published and pinned by PDFX.
- PDFX production canaries succeeded for Debbie's exact 51-, 27-, and 25-page
  PDFs. Their final maps passed download, digest, partition, range, and ABC
  checks. No page-resolution LLM or fallback range was used.
- Corrected canary headings include Acknowledgments on page 9 and Figure
  Legends/Funding/Availability on pages 5/13/13.

Authoritative upstream implementation evidence is in PDFX's tracked
`docs/superpowers/plans/2026-08-22-pdfx-primary-page-provenance.md` and
<https://github.com/alliance-genome/agr_pdf_extraction_service/pull/48>.

## 2. Current AI Curation defect

`PDFXParser` currently downloads only `merged` Markdown. It strips the response
before parsing, then `markdown_to_pipeline_elements()` assigns pages from
legacy inline page-marker syntax. Production PDFX intentionally emits official
AGR ABC Markdown without page markers, so every element and downstream chunk
defaults to page 1 even though the separate page map is correct.

The page number is already carried by the existing element schema, chunk
model, Weaviate storage code, retrieval tools, and viewer/evidence contracts.
The missing link is the PDFX merged-Markdown consumer boundary.

The live production checkout was verified read-only on 2026-08-23 as detached
at exact tag `v0.8.20` / `6df184c6`, frontend version `0.8.20`. It contains the
expected modified maintenance-message file and historical ignored backup
artifacts. Do not clean, reset, reuse, or deploy current `main` into that
checkout. Select a clean timestamped release checkout during the
maintenance-first deployment.

## 3. Non-negotiable contracts

### 3.1 Official ABC Markdown

The official AGR ABC Markdown schema in
`agr_abc_document_parsers/src/agr_abc_document_parsers/MARKDOWN_SCHEMA.md` is
authoritative. AI Curation must not add page comments, attributes, sentinels,
or any other syntax to merged Markdown.

- Download and digest-check the exact merged UTF-8 bytes.
- Do not `.strip()`, normalize, rewrite, or reserialize those bytes before
  binding them to the sidecar.
- Existing semantic text normalization for pipeline elements remains unchanged
  after the byte-to-page lookup.

### 3.2 PDFX merged sidecar

The supported producer contract is exactly:

- `schema = "pdfx-merged-page-provenance"`
- `contract_version = "merged-page-provenance-v1"`
- `GET /api/v1/extract/{process_id}/download/page_provenance`
- a canonical JSON record bound to the exact merged Markdown SHA-256 and byte
  length;
- a nonempty, contiguous partition of every merged Markdown UTF-8 byte;
- one integer `page_number` in the declared PDF page range for every range;
- a canonical `record_sha256` over all record fields except itself.

AI Curation validates the consumer-visible record, its canonical digest, exact
field set, merged-byte binding, range partition, page/candidate bounds, method
vocabulary, evidence digests, range IDs, summary counters, and LLM receipt
container. It does not redownload PDFX's private merge audit or source maps;
PDFX has already verified those before serving the process-scoped bundle.

### 3.3 Failure behavior

For a successful merged-PDFX job, missing, malformed, stale, non-partitioning,
or digest-mismatched page provenance is a parsing failure. AI Curation must not
silently fall back to page 1 or legacy inline markers.

Non-merged PDFX mode has no public merged-sidecar contract and retains its
existing configured-extractor behavior. Provider Markdown ingestion also
retains its existing contract until a provider exposes an authoritative page
sidecar.

## 4. Bounded design

### 4.1 Download once per required artifact

Refactor the existing retrying download helper just enough to retrieve exact
artifact bytes. A merged job downloads `merged` and `page_provenance`; the
Markdown is decoded as strict UTF-8 only after its exact bytes are retained for
validation. Do not introduce new retries, limits, feature flags, or
configuration values.

### 4.2 Validate without semantic rereading

Add a small AI Curation-owned validator/index for the public merged-sidecar
contract. It performs JSON/schema/hash/range/counter validation and builds an
ordered byte-range lookup. It must not import PDFX internals, parse Markdown,
use regex, use fuzzy matching, or call an LLM.

### 4.3 Assign pages during the existing Markdown pass

Extend `markdown_to_pipeline_elements()` with an optional validated page-range
index. While its existing line walk identifies an element, retain that
element's first non-whitespace UTF-8 byte offset and obtain the page from the
range containing that byte. This is the element's primary page.

For elements spanning a page boundary, the first content byte wins. This is
consistent with the existing localization policy and with PDFX's first-page
semantics for cross-page native blocks. Chunking already preserves the first
element's metadata as the chunk's primary page, so no second chunk scanner or
Weaviate schema change is needed.

Legacy page-marker handling remains only for callers without the new range
index. When the sidecar is supplied, it is authoritative.

### 4.4 Persist compact evidence

The existing processed-element JSON provides the durable per-element page
evidence. The compact PDFX JSON receipt should record the sidecar schema,
contract version, record digest, expected page count, range count, and summary;
it must not duplicate all ranges or publication text.

## 5. Scope boundaries

### In scope

- The bounded Weaviate retrieval/default and native-backup changes from AI
  Curation PR #628, ported onto exact production `v0.8.20` without unrelated
  commits from `main`.
- Local AI Curation jobs that submit PDFs to PDFX with merge enabled.
- Exact merged and page-sidecar download and validation.
- Primary page assignment during the existing Markdown-to-element pass.
- Existing element-to-chunk-to-Weaviate propagation.
- Focused unit/contract validation and one fresh multi-page dev ingestion.
- Search/document evidence showing non-page-1 chunks are returned with their
  stored primary pages.
- A separate forward-port PR that applies the proven page consumer and release
  state to current `main` after the hotfix succeeds.

### Out of scope

- PDFX producer changes, parser-package changes, or another PDFX deployment.
- Inline page markers or modifications to official ABC Markdown.
- Bounding boxes, PDF coordinates, document-item IDs, or pixel highlighting.
- Sentence-level or token-level multi-page provenance.
- Automatic rechunking of historical production documents. Existing documents
  keep their stored pages; the production proof uses a fresh extraction.
- ABC Literature/provider Markdown page provenance. Literature currently
  exposes canonical Markdown and figure metadata but not PDFX's page sidecar;
  that needs an additive provider artifact contract before AI Curation can
  consume it honestly.

## 6. Avoidance of over-engineering

Every checkbox is a release gate for this PR.

- [x] No inline page syntax or Markdown mutation.
- [x] No new regex for page inference or publication roles.
- [x] No fuzzy alignment, RapidFuzz use, PDF text scan, page-image pipeline, or
  LLM review of page numbers in AI Curation.
- [x] No second semantic Markdown parse or post-hoc element-to-text alignment.
- [x] No per-character page array; use the ordered ranges directly.
- [x] No generalized provenance framework, plugin system, compatibility layer,
  migration, feature flag, or rollback subsystem.
- [x] No Weaviate schema change when the existing `page_number` property is
  sufficient.
- [x] No changes to chunking or storage unless a focused test demonstrates a
  concrete propagation defect.
- [x] No speculative support for unversioned/future sidecar shapes.
- [x] Tests cover contract mutations and observed boundary behavior without an
  exhaustive theoretical matrix.
- [x] No intervening `main` feature/fix commits enter the production hotfix.
- [ ] The `main` forward-port does not duplicate or replay PR #628.

## 7. Acceptance criteria

### Contract and parser

- [x] Merged Markdown is retained as exact response bytes through sidecar
  validation and strict UTF-8 decoding.
- [x] The public `merged-page-provenance-v1` record is validated fail-closed,
  including its canonical record digest, merged SHA/size, exact range
  partition, page bounds, range fields, summaries, and receipt container.
- [x] A multibyte UTF-8 fixture proves byte offsets, rather than Python
  character offsets, select pages correctly.
- [x] Headings, paragraphs, lists, tables, and code blocks receive the page of
  their first non-whitespace byte.
- [x] An element spanning two ranges retains its first page.
- [x] Sidecar-supplied pages override any legacy marker state.
- [x] Missing, malformed, stale, or invalid merged sidecars raise a clear
  `PDFParsingError`; they never produce page-1 output.
- [x] Existing non-merged and provider-Markdown behavior remains covered.
- [x] No bounding-box or synthetic `provenance` metadata is introduced.

### Downstream propagation

- [x] Focused tests prove page-aware elements become chunks with the expected
  primary pages using the existing chunker.
- [x] Existing Weaviate serialization and retrieval tests remain green and no
  schema migration is required.
- [x] Processed JSON and compact PDFX JSON retain useful, nonduplicative page
  evidence.

### Validation gates

- [x] Diff the hotfix against `v0.8.20` and account for every production file:
  it must belong to the bounded PR #628 port, page consumer, tests/docs, or
  patch-release metadata.
- [x] `py_compile`, scoped Ruff if configured, `git diff --check`, and focused
  parser/chunk/Weaviate tests pass.
- [x] The repository's backend Docker gate passes on the current reviewed-code
  candidate; rerun if review or release work changes backend behavior.
- [ ] Frontend tests/type-check/build are run only if the final diff or formal
  dev-release gate requires them; any baseline-only TypeScript debt is recorded
  separately.
- [x] Secret scanning and repository PR gate pass. The hotfix gate used its
  supported missing-Ruff skip because whole-file lint against the older
  `v0.8.20` baseline exposes 277 unrelated findings in PR #628-touched files;
  scoped Ruff passed for the new consumer/parser files.

### Fresh dev proof

- [ ] Follow the `ai-curation-release` dev runbook: verify restricted AWS
  access, VPN, dev-mode boundary, and remote checkout hygiene before mutation.
- [ ] Deploy the exact reviewed candidate to a clean timestamped dev checkout
  only after the required local automated gates pass.
- [ ] Upload a fresh known multi-page PDF through the local PDFX path, recording
  the AI Curation document/job ID and PDFX process ID.
- [ ] Verify processed elements and Weaviate chunks contain multiple correct
  pages, including targeted back-matter headings when the 25- or 27-page Debbie
  canary is used.
- [ ] Run page-aware document searches and record returned chunk IDs, sections,
  and page numbers; no relevant result should be silently page 1.
- [ ] Confirm the viewer/evidence path uses the page hint honestly without
  claiming bbox precision.
- [ ] Record the exact candidate commit, evidence paths, service health, and
  final dev instance state. Stop rather than terminate dev when finished.

### Tagged production release

- [ ] After the exact reviewed hotfix commit passes dev, select the next patch version,
  update both frontend package files, and add a curator-facing changelog entry
  dated with the actual release date. Record whether this small patch should
  leave the What's New popup pinned to the last substantive release.
- [ ] Rebuild and verify the release metadata on dev, create an annotated tag
  and GitHub release from the exact dev-tested commit, then redeploy that exact
  tag to dev for the final tag check.
- [ ] Before production mutation, inspect custom agents, user-created flows,
  user prompt versions, migrations, the production checkout, and both nginx
  route targets. Record the safety summary and Chris's authorization above.
- [ ] Put the root application into externally verified maintenance mode
  before backups or checkout/deploy changes; keep the independent `/uploads/`
  route explicitly inventoried.
- [ ] Create and verify PostgreSQL and native Weaviate backups, recording exact
  backup identifiers and status.
- [ ] Deploy only the approved release tag, run migrations, verify backend,
  frontend, Weaviate, TraceReview, Langfuse, Loki, and a server-local PDF route
  while maintenance remains active.
- [ ] Restore both nginx routes correctly, pass the public PDF route preflight,
  clear maintenance, and verify version/SHA, basic chat, Audit/Agent Studio,
  Documents Library PDF rendering, and release-specific page provenance.
- [ ] Run a fresh production local-PDF extraction when a safe authorized test
  document/account path is available, then prove processed elements, stored
  chunks, and search results use multiple correct pages. If production-side
  upload cannot be performed safely, the dev canary remains the behavior proof
  and production verification must at minimum prove the exact tag and consumer
  code are live without claiming a production ingestion canary.
- [ ] Save the deployment record and curator-facing Slack release-note draft.

### Main forward-port

- [ ] Create a clean branch from then-current `origin/main` after the hotfix is
  proven; do not assume `416c62aa` remains the head.
- [ ] Apply only the page-sidecar consumer, its focused tests/docs, and the
  release-state/version changes needed to keep `main` current. PR #628 is
  already present and must not be replayed.
- [ ] Resolve forward-port conflicts against current ownership rather than
  copying hotfix-era files wholesale.
- [ ] Run focused tests plus changed-scope/full PR gates, repeat the mandatory
  Sol/xhigh `$max-review-skill` review, and obtain bounded Claude PR review.
- [ ] Merge the forward-port PR and verify `main` contains both PR #628 and the
  production-proven page consumer.

## 8. Implementation and review sequence

1. Commit this goal document before production-code changes.
2. Create the production hotfix from exact tag `v0.8.20` / `6df184c6`. Port
   only PR #628's Weaviate change onto that baseline and inspect every conflict
   and changed file; do not merge or rebase current `main` into the hotfix.
3. Add focused failing tests for the public sidecar contract, exact response
   bytes, multibyte offsets, primary-page semantics, and fail-closed behavior.
4. Implement the smallest validator/index and parser integration that makes
   those tests pass.
5. Run focused validation, inspect the diff for unnecessary code, and update
   this document's progress trail.
6. Run the final automated gates proportionate to the diff.
7. Spawn a GPT-5.6 Sol sub-agent with xhigh reasoning for the mandatory local
   review. Its prompt must explicitly invoke `$max-review-skill`, cite this
   document and the final diff, and enforce Section 6. Resolve every supported
   Blocker, Material correction, and High-value simplification. Repeat only
   after material code changes.
8. Push and open the hotfix PR only after the local verdict is `Accept` or `Accept
   with follow-ups` with no supported material finding outstanding.
9. Ask Claude to review the PR against this document. Require each requested
   change to identify a reachable defect, violated contract, or concrete data
   integrity risk and propose the smallest complete correction. Iterate only
   for supported material findings; do not turn follow-ups into scope.
10. After checks and bounded reviews pass, perform the fresh dev proof. No
   additional external reviewer approval is required for the PR workflow.
11. Merge the hotfix PR, prepare the normal patch release metadata, verify it on dev,
    and create the annotated tag/GitHub release from the exact tested commit.
12. Perform the production safety inventory and report it for visibility.
    Chris's 2026-08-23 authorization satisfies the approval gate unless the
    inventory reveals a new material risk outside this document.
13. Follow maintenance-first deployment, PostgreSQL/Weaviate backup, tagged
    deploy, server-local checks, dual-route cutover, public preflight, and
    post-deploy verification in the release runbooks.
14. After production is proven, create the clean `main` forward-port, apply
    only the page consumer/release-state delta, run its bounded tests/reviews,
    merge it, and verify `main` retains its existing PR #628 implementation.
15. Record final implementation, both PRs, dev, release, backup, production,
    and forward-port evidence here and in the dated deployment record. Stop
    dev, retain its EBS state, and close the goal only when every applicable
    checkbox is complete.

Claude review framing:

> Review this PR against
> `docs/design/2026-08-23-pdfx-page-provenance-weaviate-goal.md`. Ground every
> requested change in a reachable defect, violated contract, or concrete data
> integrity risk and recommend the smallest complete correction. Do not broaden
> the work into inline page syntax, rescanning/alignment, regex inference,
> generalized provenance, bounding boxes, provider/Literature API changes,
> historical reindexing, migrations, feature flags, or speculative edge-case
> matrices. Record unrelated ideas as non-blocking follow-ups. Stop when no
> supported Blocker or Material correction remains.

## 9. Progress trail

- 2026-08-23: Goal activated. Historical benchmark workpad, durable benchmark
  report, official ABC Markdown schema, current AI Curation parser/chunk/store
  path, production PDFX sidecar implementation, and release/dev runbooks were
  reviewed.
- 2026-08-23: Created a clean worktree and branch from exact `origin/main`
  `416c62aa`; the unrelated dirty Sentry hotfix worktree remains untouched.
- 2026-08-23: Confirmed the bounded defect: `PDFXParser` downloads only merged
  Markdown and strips it, while page assignment still depends on inline marker
  regex. Downstream page fields already exist. Provider Markdown remains a
  separate contract because ABC Literature does not expose this sidecar.
- 2026-08-23: Chris authorized a full production release. Read the release
  metadata and production deployment references and extended the goal through
  the normal tagged-release, maintenance, backup, cutover, and post-deploy
  gates. Production is not a branch-tip emergency deployment.
- 2026-08-23: Chris clarified that production is intentionally behind `main`
  and requires a cherry-picked update. Read-only live inspection verified
  production at `v0.8.20` / `6df184c6`, while `origin/main` is `416c62aa`.
  Updated the goal to build from the exact production tag, port only PR #628
  plus the page consumer, and forward-port the proven consumer to then-current
  `main` through a separate PR. No current-main deployment is allowed.
- 2026-08-23: Created `hotfix/v0.8.21-weaviate-page-provenance` from exact
  `v0.8.20`. Ported PR #628 as `6e09ad86`, preserving only its Weaviate/search
  and native-backup changes across three production-baseline conflicts. A
  focused test exposed one modern-`main` helper dependency; the exact bounded
  integer helper was added separately in `bdb3ac20` rather than importing the
  newer configuration framework.
- 2026-08-23: Implemented exact merged/page-sidecar download, a consumer-owned
  `merged-page-provenance-v1` validator/index, and first-content-byte page
  assignment during the existing Markdown walk in `dcf4d7ba`. The official
  ABC Markdown bytes are not mutated, and no regex inference, fuzzy scan,
  semantic reread, per-character map, LLM call, storage change, or schema
  migration was added.
- 2026-08-23: Focused parser/chunk tests pass 42/42; focused Weaviate/config
  tests pass 190/190 after the compatibility helper; py_compile, diff check,
  scoped Ruff, and change-local static diagnostics pass. The complete backend
  initial rerun passed 4,888 tests with 2 skips and 1 expected xfail. Installer,
  repository migration, templates, and production Compose contracts pass.
  Secret hooks pass, and the hotfix-base Agent PR gate passes 16/16 using its
  supported missing-Ruff skip; whole-file lint on PR #628-touched files exposes
  277 unrelated `v0.8.20` baseline findings, while the consumer/parser scope is
  clean.
- 2026-08-23: The mandatory GPT-5.6 Sol/xhigh `$max-review-skill` pass found
  one material fail-closed defect: JSON-valid unhashable range values could
  escape as `TypeError`, while float sizes/counters could compare equal to
  integers. Commit `b76f25d3` applies the proportional correction by ordering
  type checks before hashing/membership and requiring exact integer sizes and
  summary counters. Four focused malformed-shape regressions pass.
- 2026-08-23: The same Sol/xhigh reviewer re-ran `$max-review-skill` on
  `b76f25d3` and returned `Accept with follow-ups`, with no supported Blocker,
  Material correction, or High-value simplification remaining. Its bounded
  168-case JSON-native malformed-shape sweep produced no non-`ValueError`
  escape. Post-fix evidence is 56/56 focused tests, 4,892 backend tests passed
  with 2 skips and 1 expected xfail, clean scoped Ruff/Pyright/py_compile/diff
  checks, and a repeated hotfix-base Agent PR gate at 16/16.
- 2026-08-23: Pushed reviewed commit `fd5404c0` and opened AI Curation PR
  #629 against `hotfix/v0.8.20-sentry-production`, not `main`. GitGuardian and
  the publish no-op lane passed. The broad Unit Tests workflow is restricted
  to PRs targeting `main`/`develop`, so the clean local Docker gate remains the
  broad-suite evidence for this production-line PR.
- 2026-08-23: Claude's initial PR review confirmed the sidecar design and
  found one material provider-path regression: `str.splitlines()` recognized
  form-feed and other Unicode separators that the prior CR/LF parser retained.
  Commit `a8ca5302` replaces it with a bounded one-pass CR/LF scanner, corrects
  the sidecar retry artifact label, and removes unsupported defensive ranking
  audit fallbacks. Focused tests pass 110/110; the post-change backend gate
  passes 4,893 tests with 2 skips and 1 expected xfail.
- 2026-08-23: Claude's request to change PR #628's environment fallback/clamp
  behavior was not adopted: the code is byte-for-byte aligned with reviewed
  `main` commit `416c62aa`, follows established `v0.8.20` configuration
  patterns, and no observed failure supports hotfix-only divergence. The
  schema-health, constant, empty-query, and log-volume notes were likewise
  non-blocking and outside this bounded release.
- 2026-08-23: The mandatory post-material-change Sol/xhigh
  `$max-review-skill` review accepted `a8ca5302` with no supported Blocker,
  Material correction, or High-value simplification. Its independent
  137,257-case CR/LF/multibyte/form-feed/NEL/Unicode-separator comparison found
  exact legacy segmentation and original-byte starts. It also confirmed the
  environment-setting disposition is correct and that ranking snapshot inputs
  satisfy their tightened invariants.
- 2026-08-23: Claude's bounded re-review used the correct production-hotfix
  base and approved the candidate with no remaining blocking or material
  finding. It independently matched legacy CR/LF segmentation and exact byte
  starts across 200,000 randomized mixed-delimiter/multibyte cases, traced all
  ranking-snapshot call-site invariants, and accepted the PR #628 configuration
  disposition. PR #629 is ready for the fresh dev proof.
- 2026-08-23: Chris explicitly reconfirmed that completing the production
  release is not the end of this goal. After production proof, create, review,
  and merge the separate forward-port so then-current `main` contains the
  production-proven page consumer without replaying its existing PR #628 work.

## 10. Resume checkpoint

Point a new Codex session at this document and say:

**Resume the PDFX page provenance to Weaviate goal from Section 10. Follow the
unchecked acceptance criteria and Section 8 in order.**

Current next action: deploy exact reviewed hotfix commit `7294efc3` to a clean
timestamped dev checkout, perform the fresh multi-page extraction proof, and
record the resulting document/job/PDFX process and Weaviate page evidence.
