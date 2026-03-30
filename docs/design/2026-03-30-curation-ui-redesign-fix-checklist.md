# Curation UI Redesign Fix Checklist

Date: 2026-03-30
Branch: `feature/entity-tag-table-redesign`
Related docs:
- `docs/design/2026-03-30-curation-ui-redesign-design.md`
- `docs/design/2026-03-30-curation-ui-redesign-plan.md`

## Review Findings To Fix

- [x] Replace UI-only entity table mutations with workspace-backed mutations so accept, reject, edit, and manual add update the real curation workspace state.
- [x] Remove the hardcoded candidate bridge values that force `db_status: not_found`, `entity_type: ATP:0000005`, and blank metadata for every row when real candidate validation data exists.
- [x] Reconnect row selection to the workspace route and hydration state so `/curation/:sessionId/:candidateId` restores the matching selected row and evidence pane.
- [x] Remove new fallback behavior in the entity-table flow so incomplete data fails clearly instead of silently collapsing to placeholder values.
- [x] Align the new table with the spec vocabulary and edit contract.
- [x] Use `Source` as the header label.
- [x] Start manual add rows with blank editable fields.
- [x] Keep PDF navigation click-only.
- [x] Keep visible wording as `Show in PDF`.

## Implementation Tasks

- [x] Add tested mapping helpers between `CurationCandidate` and `EntityTag`.
- [x] Refactor entity-table state so only transient UI state stays local.
- [x] Wire row selection through workspace `activeCandidateId`.
- [x] Wire row accept/reject through `submitCurationCandidateDecision()`.
- [x] Wire batch accept through real candidate decision updates.
- [x] Wire inline edit save through draft update plus candidate validation refresh.
- [x] Wire manual add save through `createManualCurationCandidate()`.
- [x] Remove render fallbacks from row/type/status display.
- [x] Update table tests for controlled selection and real callback flow.
- [x] Update page tests for route-driven selection and validation-backed rendering.

## Validation

- [x] Run targeted entity table and workspace page frontend tests.
- [x] Run the full frontend Vitest suite locally with `cd frontend && npm run test -- --run`.
- [x] Run backend contract validation for native entity tags:
  `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/schemas/test_curation_workspace.py tests/unit/lib/curation_workspace/test_session_service.py -q -k 'entity_tags or workspace_response'"`
- [x] Update this checklist with completed items before handing off.

## Completed Follow-Up

- [x] Replace the page-level candidate-to-tag bridge by moving native `entity_tags` into the backend workspace payload and consuming them directly in `CurationWorkspacePage`.
- [x] Remove or archive legacy right-panel components that are now unused by `CurationWorkspacePage` when no other routes still depend on them.
