# Durable Chat Rollout Verification

Audit date: 2026-04-21

This note records the ALL-242 verification pass for the KANBAN-1206 durable chat
history rollout.

## Summary

- Focused frontend durable-chat coverage passed across `/history`, inline
  transcript expansion, resume hydration, `Chat.tsx` terminal recovery and
  assistant-rescue behavior, AppBar history routing, legacy cache cleanup
  helpers, and logout cleanup.
- `frontend` production build passed from the workspace checkout.
- Docker-based backend validation upgraded cleanly to Alembic head
  `y8z9a0b1c2d3` and reran the durable history plus assistant-rescue contract
  tests successfully.
- The Symphony review stack came up healthy at `http://127.0.0.1:3242/` and
  `http://127.0.0.1:8242/health`.
- Browser QA passed for the AppBar history entry point, history search,
  inline transcript expansion, rename, individual delete, bulk-delete, and
  durable resume hydration from `/?session=<session_id>`.
- Additional rollout-gate QA passed for cross-device durable resume, live
  chat turn replay/idempotency, durable flow replay, interrupted terminal
  markers, assistant-rescue failure UI behavior, and generated-title versus
  manual-title race handling.
- One focused follow-up issue was created for a failed cleanup assumption:
  `ALL-249` tracks the dev-mode bootstrap path leaving the five legacy chat
  `localStorage` keys in place after a full page reload.

## Scope Checklist

- [x] Refresh the frontend build.
- [x] Run focused frontend tests covering history, resume hydration, terminal
  recovery, assistant-rescue UI behavior, and the AppBar history route.
- [x] Validate the rollout migration head in the isolated Docker test stack.
- [x] Exercise browser QA for history search, inline transcript expansion,
  rename, individual delete, bulk-delete, AppBar navigation, and durable resume.
- [x] Complete direct manual/browser coverage for cross-device resume,
  interrupted markers, assistant-rescue failure UI, flow replay, turn
  idempotency, and title-race behavior.
- [x] Record the verification outcome and spin out the legacy-cache cleanup
  failure as a focused follow-up ticket.

## Automated Validation

Commands run:

```bash
cd frontend && npm ci
cd frontend && npm run test -- --run \
  src/features/history/HistoryPage.test.tsx \
  src/features/history/ConversationTranscriptView.test.tsx \
  src/features/history/TranscriptMessage.test.tsx \
  src/pages/HomePage.test.tsx \
  src/test/components/Chat.test.tsx \
  src/App.test.tsx \
  src/lib/chatCacheKeys.test.ts \
  src/App.logout.test.tsx
cd frontend && npm run build
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  bash -lc "python -m pytest tests/unit/test_chat_history_schema_migration.py -v --tb=short"
docker compose -f docker-compose.test.yml run --rm backend-contract-tests \
  bash -lc "alembic upgrade head && alembic heads && alembic current && \
    python -m pytest tests/contract/test_chat_history.py \
      tests/contract/test_chat_assistant_rescue.py -v --tb=short"
```

Results:

- Frontend focused suite: `8` files, `70` tests passed.
- Frontend build: passed.
- Backend durable chat schema migration unit test: `2` tests passed.
- Backend contract checks: `9` tests passed.
- Alembic head/current after upgrade: `y8z9a0b1c2d3 (head)`.

## Browser Validation

The browser pass used the Symphony-managed review stack prepared from this
workspace.

- A brand-new browser context loaded
  `/?session=cfd38fff-160e-43fe-af19-a95ec636e816` and restored the durable
  transcript without any prior local chat cache; only the namespaced
  `chat-cache:v1:dev-user-123:*` keys were repopulated in that fresh context.
- The AppBar `Chat History` entry opened `/history`.
- `/history` listed durable sessions and expanded the stored transcript inline
  for the live verification conversation.
- Search filtered the list down to the matching durable session.
- Rename updated the visible session title in-place.
- Individual delete removed the targeted session.
- Bulk-delete removed the selected sessions and left the non-selected session in
  place.
- Loading `/?session=cfd38fff-160e-43fe-af19-a95ec636e816` after clearing the
  namespaced `chat-cache:v1:dev-user-123:*` entries restored the durable
  transcript and repopulated the namespaced session/message cache.
- The first live chat turn generated the expected durable session title
  `Hello from ALL-242 durable history verification` and the title was visible in
  `/history` during this pass.
- A live backend replay check posted the same `turn_id` twice to
  `/api/chat/stream`; the second response replayed the stored assistant output
  instead of rerunning the chat, and `/api/chat/history/<session_id>` still held
  exactly one user row plus one assistant row for that durable turn.
- A live backend flow replay check created one temporary `chat_output` flow,
  executed it twice with the same `turn_id` through `/api/chat/execute-flow`,
  and confirmed the replay response skipped a second durable execution:
  `execution_count` stayed at `1`, while durable history remained one user row,
  one flow evidence row, and one flow summary row for the turn.
- Title-race stress used two new durable sessions: one was renamed
  immediately after its first turn completed, while the other was left on the
  generated-title path. Repeated `/api/chat/history` reads plus `/history`
  reloads kept the manual title on the renamed session and the generated title
  on the second session, with no reversion or cross-session mix-up.

## Targeted Failure-UI QA

These two terminal-state checks were executed in isolated Playwright browser
contexts with route stubs on the review stack. The frontend failure UI is only
reachable on rare terminal paths, so this was the safest way to verify the
actual rendered behavior without mutating shared backend state.

- Interrupted marker UI: a stubbed `/api/chat/stream` response emitted
  `TEXT_MESSAGE_CONTENT` followed by `turn_interrupted`. The chat surface kept
  the partial assistant content and showed the expected message
  `The response was interrupted before it could be saved.` on that same turn.
- Assistant-rescue failure UI: a stubbed `/api/chat/stream` response emitted
  `turn_save_failed`, and the follow-on `/api/chat/<session_id>/assistant-rescue`
  call returned `500 {"detail":"database unavailable"}`. The chat surface kept
  the streamed assistant text and surfaced the expected durable-save error
  `This response is shown above, but it could not be saved to chat history: database unavailable`.

## Finding

### 1. Dev-mode bootstrap leaves legacy chat keys behind

- Repro:
  - Set `chat-messages`, `chat-session-id`, `chat-active-document`,
    `chat-user-id`, and `pdf-viewer-session` in browser `localStorage`.
  - Clear the namespaced `chat-cache:v1:dev-user-123:*` keys.
  - Hard-load `/?session=<durable-session-id>` in the local review stack.
- Expected:
  - The legacy keys are removed during frontend bootstrap before or during
    durable resume.
- Actual:
  - Durable resume succeeds and repopulates namespaced storage, but the five
    legacy keys remain present in `localStorage`.
- Follow-up:
  - `ALL-249`: KANBAN-1206: Clear legacy chat localStorage keys during
    dev-mode durable session bootstrap.
