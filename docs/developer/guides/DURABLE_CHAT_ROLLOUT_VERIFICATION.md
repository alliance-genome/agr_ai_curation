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
- [x] Record the verification outcome and spin out the legacy-cache cleanup
  failure as a focused follow-up ticket.
- [ ] Complete direct manual/browser coverage for interrupted markers,
  assistant-rescue failure UI, flow replay, and title-race stress beyond the
  single happy-path run captured here.

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
