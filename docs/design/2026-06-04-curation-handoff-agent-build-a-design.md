# Build A — Auto-Push Curation Handoff Agent (A2) — Design

**Date:** 2026-06-04
**Status:** Design / spec (decisions settled in `2026-06-04-curation-interface-diagnosis.md` §5, §9, §10;
open questions below need answers before implementation planning).
**Verified against:** `main` working tree (branch `curation-interface-diagnosis`). All file:line cites read directly.
**Scope:** Backend only — independent of Build B (the review-screen redesign); can proceed in parallel.

## 1. Goal

Make a **flow or batch run** that terminates in a dedicated **`curation_handoff`** agent automatically run
the full curation pipeline (envelope materialization + post-curation pipeline) and **auto-create the
review session(s)** on completion — so a curator opens the curation UI and the items are already waiting,
no manual "Review & Curate" click. This closes three gaps:

1. **Batch** flows are hard-blocked at build time from terminating in curation
   (`backend/src/lib/batch/validation.py:58-66` requires `file_output`).
2. **Batch processor** hard-fails any run that does not return a `FILE_READY` URL
   (`backend/src/lib/batch/processor.py:226-228`).
3. **Flow** `curation_prep` step only stages the envelope (stage 1) and never creates a session
   (`backend/src/lib/flows/executor.py:2092-2108`); the session is left to a manual `bootstrap` a
   flow/batch run never makes.

A2 adds a **new dedicated terminal agent** (not a change to `curation_prep`) so it owns the batch/flow
success contract cleanly and leaves the chat `curation_prep` path untouched. (Codex 5.5 and the diagnosis
both favored A2.)

## 2. Scope — Suggested Starting Locations (likely touchpoints, not exhaustive)

1. **New terminal agent** — `config/agents/curation_handoff/agent.yaml` (mirror `config/agents/curation_prep/agent.yaml`
   and the terminal pattern of `packages/alliance/agents/tsv_formatter/agent.yaml`). Declares a new
   `batch_capabilities: [curation_handoff]`; `supervisor_routing.enabled: false`; `requires_document: true`;
   `required_params: [document_id]`. The capability flows registry → `AGENT_REGISTRY`
   (`backend/src/lib/agent_studio/registry_builder.py:114`), read by `has_batch_capability`
   (`validation.py:30-35`) — no registry-loader change needed.
2. **Batch validation** — `backend/src/lib/batch/validation.py:58-66`: accept a `curation_handoff` exit
   node as legal (alongside `file_output`); keep the `chat_output` rejection; PDF-extraction requirement
   (`:47-56`) unchanged.
3. **Batch processor** — `backend/src/lib/batch/processor.py:226-228` (the single `FILE_READY` hard-fail)
   and `_execute_flow_for_document` (`:313-404`, which today only captures `FILE_READY`): accept a
   "curation ready / sessions created" success signal and return it.
4. **Flow executor** — `backend/src/lib/flows/executor.py:2065-2115` (the existing `curation_prep` tool to
   model on), terminal/`FLOW_FINISHED` handling (`:2927-3012`), output-node classification (`:158-181`).
5. **Reuse the existing two-stage prep** — `run_curation_prep` (`curation_prep_service.py:75-136`,
   one-adapter enforcement `:464-475`); `run_post_curation_pipeline` (`pipeline.py:90-111`, `created_by_id`
   threaded `:103,:350`); and especially the **chat per-adapter loop** `prepare_chat_curation_sessions`
   (`bootstrap_service.py:92-136`) which runs prep then loops adapters calling `bootstrap_document_session`
   — this is the exact reusable shape for auto-push.
6. **Identity** — batch resolves `cognito_sub = user.auth_sub` and passes `user_id=cognito_sub`,
   `db_user_id=batch.user_id` (`processor.py:128-135, :319-321`). Sessions store `created_by_id` as
   `String()` (`models.py:105`), stamped from the Cognito subject on the chat path
   (`bootstrap_service.py:177-179`).

## 3. Out of Scope — Do NOT Touch

- `config/agents/curation_prep/agent.yaml` and the chat `curation_prep` path — A2 adds a sibling, it does
  not change `curation_prep` semantics.
- The submission gate (`session_submission_service.py:785,801`) — load-bearing and correct (diagnosis §7).
- The front-end "Review & Curate" affordance (`PreparedReviewAndCurateButton`, `BatchPage.tsx:1075,1167`)
  and `bootstrap-availability`/`bootstrap` — already work; keep as a manual fallback.
- The `FILE_READY` path and file-output formatters — file-output batch flows must behave exactly as today;
  the processor change is **additive**.
- Review-row materialization / `workspace_display` (`materialization.py`) and the field audit — that's Build B.
- Inventory-table UI / ALL-557 scoping logic — A2 only stamps the owner correctly.

## 4. Design

- **New agent + capability:** `agent_id: curation_handoff`, `batch_capabilities: [curation_handoff]`,
  terminal (non-routable), `requires_document`. Accurate description (it *does* create review sessions —
  unlike the stale `curation_prep` YAML prose the diagnosis flagged). Shows in the palette as a curation
  terminal sibling to the file formatters.
- **Batch validation:** accept `curation_handoff` as a legal exit capability.
- **Batch processor success-signal:** a run succeeds on **either** a `FILE_READY` URL **or** a
  curation-ready signal (sessions created). `_execute_flow_for_document` captures the curation-ready
  signal from the flow event stream and returns it; success stored on `BatchDocument`.
- **Flow-end pipeline trigger:** the handoff agent's flow tool runs **stage 1** (`run_curation_prep`) then
  **stage 2** (`run_post_curation_pipeline`) per adapter — i.e. it does in-run what
  `prepare_chat_curation_sessions` does for chat. The agent emits no `FILE_READY`, so the flow terminates
  via the natural fallback (`executor.py:2979-2995`) and reports via `FLOW_FINISHED` (`:2996-3012`); emit a
  `CURATION_READY` event (with created session ids) and/or attach session ids to `FLOW_FINISHED.data`.
  Transaction parity with the chat path (`manage_transaction=False` per adapter, then one commit).
- **Per-adapter sessions:** loop adapters like `prepare_chat_curation_sessions` over `adapter_keys`
  (`bootstrap_service.py:108`); `run_curation_prep` enforces one adapter per call, so N adapters → N sessions.
- **Identity/ownership:** stamp `created_by_id` with the run's **Cognito subject** (matches chat path;
  what ALL-557 "my inventory" filters on). `db_user_id` stays for agent-visibility only.

## 5. Development Guardrails

- Forward-only; no compatibility shims or defensive fallbacks (project rule). Additive to batch — file-output
  flows unaffected. Project-agnostic — reuse the domain-pack-metadata-driven prep+pipeline, no per-domain
  branching. Reuse, don't fork, the prep→pipeline orchestration. No `--no-verify` / hook bypass.

## 6. Open Questions (need answers before the implementation plan)

1. **Success-signal shape:** new `CURATION_READY` event (with `session_ids`/`adapter_keys`) vs. enriching
   `FLOW_FINISHED.data` (already carries `status`+`adapter_keys`, `executor.py:3010`). What does the batch
   processor consume?
2. **`BatchDocument` storage:** reuse `result_file_path` as a curation marker, or add a column
   (`curation_session_ids` / `outcome_kind`) so the UI can distinguish file vs curation outcomes? (New
   column = a migration.)
3. **Capability name:** `curation_handoff` (proposed) vs `curation_output`/`curation_terminal` — becomes a
   stable token in YAML + validation.
4. **Multi-adapter session naming:** with one session per adapter, what default notes/tags/display so the
   curator can tell them apart in inventory?
5. **Flow path in the same change?** Diagnosis §10.2 says auto-push applies to flows too; confirm the
   standalone (non-batch) flow run also triggers the pipeline (the trigger lives in the flow tool, so it
   would).
6. **Re-run dedupe:** stage 2 already has reuse logic (`find_reusable_prepared_session`,
   `bootstrap_service.py:158-164`). Confirm an auto-push re-run updates the existing session rather than
   duplicating; dedupe key = document + adapter + flow_run_id?
7. **Trigger location:** inside the flow tool (keeps transaction context) vs a post-loop hook in
   `execute_flow` (cleaner, needs completed-step candidates + fresh session).
8. **Partial failure:** in a multi-adapter run, if one adapter's stage-2 fails — fail the whole document or
   succeed-with-warnings? (Chat path rolls back the whole transaction.)

## 7. Acceptance Criteria

- [ ] A flow ending in `curation_handoff` passes `validate_flow_for_batch` (no "must end with a file output agent").
- [ ] A batch run over it completes (no `FILE_READY` RuntimeError); each `BatchDocument` is COMPLETED with a curation-ready outcome.
- [ ] After the run, review session(s) exist per document — one per adapter — populated, visible via `GET /api/curation-workspace/sessions`, with no manual "Review & Curate".
- [ ] Each session's `created_by_id` = the Cognito subject of the runner (appears under their "my inventory", ALL-557).
- [ ] Multi-adapter flow → one session per adapter.
- [ ] Existing file-output batch flows behave exactly as before (no regression).
- [ ] The standalone (non-batch) flow path ending in the handoff agent also creates sessions.
- [ ] Re-running over the same document/adapter does not duplicate sessions.

## 8. Validation

- [ ] Unit: `validate_flow_for_batch` accepts `curation_handoff`, still rejects `chat_output`/capability-less exits.
- [ ] Unit: processor treats curation-ready as success; still hard-fails a non-curation flow with no `FILE_READY`.
- [ ] Unit: per-adapter loop creates N sessions; `created_by_id` stamped with the Cognito subject.
- [ ] Integration (**must** use `docker-compose.test.yml`, never the live backend container):
      `docker compose -f docker-compose.test.yml run --rm backend-integration-tests` — end-to-end batch over a
      `curation_handoff` flow producing sessions; multi-adapter; idempotent re-run.
- [ ] Manual: on dev, build a flow ending in the handoff agent, run a small batch, confirm sessions appear
      in the inventory under the runner's identity without clicking "Review & Curate".
