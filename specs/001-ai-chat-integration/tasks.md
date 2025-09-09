# Tasks: AI Chat Integration

**Input**: Design documents from `/specs/001-ai-chat-integration/`
**Prerequisites**: plan.md (✓), research.md (✓), data-model.md (✓), contracts/ (✓)

## Execution Flow (main)

```
1. Load plan.md from feature directory
   → SUCCESS: Found implementation plan with FastAPI/React tech stack
2. Load optional design documents:
   → data-model.md: Extracted ChatHistory extensions, AIConfiguration
   → contracts/: Found chat-api.yaml → contract test tasks
   → research.md: Extracted OpenAI unified approach → setup tasks
3. Generate tasks by category:
   → Setup: dependencies, environment, database migration
   → Tests: contract tests, integration tests for streaming/models
   → Core: AI services, streaming endpoints, model selection UI
   → Integration: error handling, configuration management
   → Polish: performance validation, documentation
4. Apply task rules:
   → Different files = marked [P] for parallel execution
   → Database changes before model updates
   → Tests before implementation (TDD)
5. Number tasks sequentially (T001, T002...)
6. Generate dependency graph and parallel execution groups
7. SUCCESS: 23 tasks ready for execution
```

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Web application structure: `backend/` and `frontend/` directories

## Phase 3.1: Setup & Dependencies

- [ ] T001 Install OpenAI Python SDK in backend/requirements.txt
- [ ] T002 [P] Add AI provider environment variables to .env.example
- [ ] T003 Add database migration for ChatHistory model extensions

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3

**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**

### Contract Tests

- [ ] T004 [P] Contract test POST /chat/ endpoint in backend/tests/contract/test_chat_post.py
- [ ] T005 [P] Contract test POST /chat/stream endpoint in backend/tests/contract/test_chat_stream.py
- [ ] T006 [P] Contract test GET /chat/models endpoint in backend/tests/contract/test_chat_models.py

### Integration Tests

- [ ] T007 [P] Integration test basic AI response (OpenAI) in backend/tests/integration/test_ai_chat_basic.py
- [ ] T008 [P] Integration test model selection (Gemini) in backend/tests/integration/test_ai_chat_models.py
- [ ] T009 [P] Integration test streaming response in backend/tests/integration/test_ai_chat_streaming.py
- [ ] T010 [P] Integration test conversation persistence in backend/tests/integration/test_chat_persistence.py

### Frontend Tests

- [ ] T011 [P] Frontend test model selection dropdown in frontend/src/components/**tests**/ModelSelector.test.tsx
- [ ] T012 [P] Frontend test streaming message display in frontend/src/components/**tests**/StreamingMessage.test.tsx

## Phase 3.3: Core Implementation (ONLY after tests are failing)

### Database & Models

- [ ] T013 Run ChatHistory table migration to add model_provider and model_name columns
- [ ] T014 [P] Update ChatHistory model in backend/app/models/chat.py
- [ ] T015 [P] Create AI configuration models in backend/app/models/ai_config.py

### AI Service Layer

- [ ] T016 [P] Create OpenAI service client in backend/app/services/openai_service.py
- [ ] T017 [P] Create Gemini service client (OpenAI-compatible) in backend/app/services/gemini_service.py
- [ ] T018 Create unified AI service factory in backend/app/services/ai_service_factory.py

### Backend API Endpoints

- [ ] T019 Update POST /chat/ endpoint with AI integration in backend/app/routers/chat.py
- [ ] T020 Implement POST /chat/stream endpoint with Server-Sent Events in backend/app/routers/chat.py
- [ ] T021 Create GET /chat/models endpoint in backend/app/routers/chat.py

### Frontend Components

- [ ] T022 [P] Create model selection dropdown component in frontend/src/components/ModelSelector.tsx
- [ ] T023 [P] Create streaming message component in frontend/src/components/StreamingMessage.tsx
- [ ] T024 Update ChatInterface component with model selection and streaming in frontend/src/components/ChatInterface.tsx

## Phase 3.4: Integration & Error Handling

- [ ] T025 Add AI service error handling and retry logic in backend/app/services/
- [ ] T026 [P] Add frontend error boundaries for AI failures in frontend/src/components/ErrorBoundary.tsx
- [ ] T027 Configure environment variables and settings management

## Phase 3.5: Polish & Validation

- [ ] T028 [P] Add unit tests for AI service factory in backend/tests/unit/test_ai_service_factory.py
- [ ] T029 [P] Performance test streaming response latency in backend/tests/performance/test_streaming_performance.py
- [ ] T030 Run quickstart validation scenarios from quickstart.md
- [ ] T031 [P] Update API documentation in backend/app/docs/
- [ ] T032 Code cleanup and remove any debug logging

## Dependencies

### Critical Path

- T001-T003 (Setup) → T004-T012 (Tests) → T013-T015 (Models) → T016-T018 (Services) → T019-T021 (Endpoints) → T022-T024 (Frontend)

### Specific Dependencies

- T001 blocks T016, T017 (OpenAI SDK required for AI services)
- T013 blocks T014 (migration before model updates)
- T014 blocks T019, T020, T021 (model changes before API endpoints)
- T016, T017, T018 block T019, T020, T021 (services before endpoints)
- T019, T020, T021 block T022, T023, T024 (backend API before frontend)
- T022, T023 block T024 (components before integration)
- Implementation (T013-T024) blocks Polish (T025-T032)

## Parallel Execution Examples

### Setup Phase (can run together after T003 completes):

```
Task: "Install OpenAI Python SDK in backend/requirements.txt"
Task: "Add AI provider environment variables to .env.example"
```

### Contract Tests (can all run in parallel):

```
Task: "Contract test POST /chat/ endpoint in backend/tests/contract/test_chat_post.py"
Task: "Contract test POST /chat/stream endpoint in backend/tests/contract/test_chat_stream.py"
Task: "Contract test GET /chat/models endpoint in backend/tests/contract/test_chat_models.py"
```

### Integration Tests (can all run in parallel):

```
Task: "Integration test basic AI response (OpenAI) in backend/tests/integration/test_ai_chat_basic.py"
Task: "Integration test model selection (Gemini) in backend/tests/integration/test_ai_chat_models.py"
Task: "Integration test streaming response in backend/tests/integration/test_ai_chat_streaming.py"
Task: "Integration test conversation persistence in backend/tests/integration/test_chat_persistence.py"
```

### Model Creation (can run in parallel after migration):

```
Task: "Update ChatHistory model in backend/app/models/chat.py"
Task: "Create AI configuration models in backend/app/models/ai_config.py"
```

### AI Services (can run in parallel):

```
Task: "Create OpenAI service client in backend/app/services/openai_service.py"
Task: "Create Gemini service client (OpenAI-compatible) in backend/app/services/gemini_service.py"
```

### Frontend Components (can run in parallel):

```
Task: "Create model selection dropdown component in frontend/src/components/ModelSelector.tsx"
Task: "Create streaming message component in frontend/src/components/StreamingMessage.tsx"
```

## Notes

- [P] tasks = different files, no shared dependencies
- Verify contract/integration tests FAIL before implementing
- OpenAI SDK works for both OpenAI and Gemini (unified approach)
- Database migration is minimal (2 new optional columns)
- All existing functionality must remain unchanged
- Commit after each task completion

## Validation Checklist

_GATE: All must be ✓ before considering tasks complete_

- [x] All contracts (chat-api.yaml) have corresponding tests (T004-T006)
- [x] All entities (ChatHistory, AIConfiguration) have model tasks (T014-T015)
- [x] All tests come before implementation (T004-T012 → T013-T032)
- [x] Parallel tasks truly independent (different files)
- [x] Each task specifies exact file path
- [x] No task modifies same file as another [P] task
- [x] TDD order enforced: failing tests → implementation → validation
