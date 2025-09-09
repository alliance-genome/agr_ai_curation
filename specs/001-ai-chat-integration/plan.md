# Implementation Plan: AI Chat Integration

**Branch**: `001-ai-chat-integration` | **Date**: September 8, 2025 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-ai-chat-integration/spec.md`

## Execution Flow (/plan command scope)

```
1. Load feature spec from Input path
   → If not found: ERROR "No feature spec at {path}"
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   → Detect Project Type from context (web=frontend+backend, mobile=app+api)
   → Set Structure Decision based on project type
3. Evaluate Constitution Check section below
   → If violations exist: Document in Complexity Tracking
   → If no justification possible: ERROR "Simplify approach first"
   → Update Progress Tracking: Initial Constitution Check
4. Execute Phase 0 → research.md
   → If NEEDS CLARIFICATION remain: ERROR "Resolve unknowns"
5. Execute Phase 1 → contracts, data-model.md, quickstart.md, agent-specific template file (e.g., `CLAUDE.md` for Claude Code, `.github/copilot-instructions.md` for GitHub Copilot, or `GEMINI.md` for Gemini CLI).
6. Re-evaluate Constitution Check section
   → If new violations: Refactor design, return to Phase 1
   → Update Progress Tracking: Post-Design Constitution Check
7. Plan Phase 2 → Describe task generation approach (DO NOT create tasks.md)
8. STOP - Ready for /tasks command
```

**IMPORTANT**: The /plan command STOPS at step 7. Phases 2-4 are executed by other commands:

- Phase 2: /tasks command creates tasks.md
- Phase 3-4: Implementation execution (manual or via tools)

## Summary

Replace the existing stub chat responses in the biological paper curation platform with real AI integration. The feature will add OpenAI and Gemini API support with streaming responses, model selection, and maintain existing database storage patterns. All current UI components and database schemas remain unchanged.

## Technical Context

**Language/Version**: Python 3.12 (backend), TypeScript/React 18 (frontend)  
**Primary Dependencies**: FastAPI, SQLAlchemy, React, Material-UI, Vite, Axios  
**Storage**: PostgreSQL (existing ChatHistory table), Docker volumes  
**Testing**: pytest (backend), Vite test suite (frontend)  
**Target Platform**: Docker containerized web application (Linux containers)
**Project Type**: web - existing frontend + backend structure  
**Performance Goals**: <2s initial response, streaming <500ms between tokens  
**Constraints**: Must work in existing Docker setup, no database schema changes, preserve all existing UI/UX  
**Scale/Scope**: Single-user development environment, extending existing chat interface

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-check after Phase 1 design._

**Simplicity**:

- Projects: 2 (backend API, frontend UI) ✅ (under max 3)
- Using framework directly? ✅ (FastAPI direct, React direct, no wrapper classes)
- Single data model? ✅ (extends existing ChatHistory, minimal new models)
- Avoiding patterns? ✅ (no Repository/UoW, direct SQLAlchemy usage)

**Architecture**:

- EVERY feature as library? ⚠️ (extending existing monolithic web app - justified by constraint)
- Libraries listed: ai_chat_service (AI integration), streaming_service (SSE streaming)
- CLI per library: N/A (web application, not CLI-based architecture)
- Library docs: Will document API contracts in OpenAPI format

**Testing (NON-NEGOTIABLE)**:

- RED-GREEN-Refactor cycle enforced? ✅ (contract tests written first, must fail)
- Git commits show tests before implementation? ✅ (TDD order enforced)
- Order: Contract→Integration→E2E→Unit strictly followed? ✅ (plan specifies this order)
- Real dependencies used? ✅ (actual PostgreSQL, real AI APIs for integration tests)
- Integration tests for: contract changes ✅, shared schemas ✅
- FORBIDDEN: Implementation before test, skipping RED phase ✅ (enforced in task ordering)

**Observability**:

- Structured logging included? ✅ (FastAPI logging, AI service response logging)
- Frontend logs → backend? ✅ (error tracking through API calls)
- Error context sufficient? ✅ (API error details, AI service failure context)

**Versioning**:

- Version number assigned? ✅ (0.2.0 - MINOR increment for new AI feature)
- BUILD increments on every change? ✅ (will increment for each task completion)
- Breaking changes handled? ✅ (backward compatible, existing API unchanged)

## Project Structure

### Documentation (this feature)

```
specs/[###-feature]/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)

```
# Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure]
```

**Structure Decision**: Option 2 - Web application (existing backend/ and frontend/ directories detected)

## Phase 0: Outline & Research

1. **Extract unknowns from Technical Context** above:
   - For each NEEDS CLARIFICATION → research task
   - For each dependency → best practices task
   - For each integration → patterns task

2. **Generate and dispatch research agents**:

   ```
   For each unknown in Technical Context:
     Task: "Research {unknown} for {feature context}"
   For each technology choice:
     Task: "Find best practices for {tech} in {domain}"
   ```

3. **Consolidate findings** in `research.md` using format:
   - Decision: [what was chosen]
   - Rationale: [why chosen]
   - Alternatives considered: [what else evaluated]

**Output**: research.md with all NEEDS CLARIFICATION resolved

## Phase 1: Design & Contracts

_Prerequisites: research.md complete_

1. **Extract entities from feature spec** → `data-model.md`:
   - Entity name, fields, relationships
   - Validation rules from requirements
   - State transitions if applicable

2. **Generate API contracts** from functional requirements:
   - For each user action → endpoint
   - Use standard REST/GraphQL patterns
   - Output OpenAPI/GraphQL schema to `/contracts/`

3. **Generate contract tests** from contracts:
   - One test file per endpoint
   - Assert request/response schemas
   - Tests must fail (no implementation yet)

4. **Extract test scenarios** from user stories:
   - Each story → integration test scenario
   - Quickstart test = story validation steps

5. **Update agent file incrementally** (O(1) operation):
   - Run `/scripts/update-agent-context.sh [claude|gemini|copilot]` for your AI assistant
   - If exists: Add only NEW tech from current plan
   - Preserve manual additions between markers
   - Update recent changes (keep last 3)
   - Keep under 150 lines for token efficiency
   - Output to repository root

**Output**: data-model.md, /contracts/\*, failing tests, quickstart.md, agent-specific file

## Phase 2: Task Planning Approach

_This section describes what the /tasks command will do - DO NOT execute during /plan_

**Task Generation Strategy**:

- Load `/templates/tasks-template.md` as base
- Generate tasks from Phase 1 design docs (contracts, data model, quickstart)
- Database migration tasks (add model_provider/model_name columns) [P]
- Backend API contract tests for streaming and model selection [P]
- AI service integration tasks (OpenAI, Gemini clients) [P]
- Frontend model selection UI tasks [P]
- Streaming response implementation tasks
- Integration tests for each user story from spec
- Error handling and validation tasks

**Ordering Strategy**:

- TDD order: Contract tests → Integration tests → Implementation
- Dependency order: Database schema → Models → Services → API → Frontend
- Backend and frontend tasks can run in parallel after models complete
- Mark [P] for parallel execution (independent components)

**Specific Task Categories**:

1. **Database Tasks**: Schema migration, model updates
2. **Backend API Tasks**: Streaming endpoints, model selection, AI integration
3. **Frontend Tasks**: Model dropdown, streaming display, loading states
4. **Integration Tasks**: End-to-end chat flow validation
5. **Error Handling**: API failures, rate limiting, validation

**Estimated Output**: 20-25 numbered, ordered tasks in tasks.md

**Key Dependencies**:

- Database schema changes must complete before backend model updates
- AI service integration must complete before streaming implementation
- Backend streaming endpoint must complete before frontend streaming UI
- Contract tests must exist and fail before implementation begins

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation

_These phases are beyond the scope of the /plan command_

**Phase 3**: Task execution (/tasks command creates tasks.md)  
**Phase 4**: Implementation (execute tasks.md following constitutional principles)  
**Phase 5**: Validation (run tests, execute quickstart.md, performance validation)

## Complexity Tracking

_Fill ONLY if Constitution Check has violations that must be justified_

| Violation                              | Why Needed                                             | Simpler Alternative Rejected Because                                                |
| -------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| Monolithic web app (not library-based) | Must extend existing codebase without breaking changes | Creating separate libraries would require major refactoring of existing chat system |

## Progress Tracking

_This checklist is updated during execution flow_

**Phase Status**:

- [x] Phase 0: Research complete (/plan command)
- [x] Phase 1: Design complete (/plan command)
- [x] Phase 2: Task planning complete (/plan command - describe approach only)
- [ ] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:

- [x] Initial Constitution Check: PASS (with documented justification)
- [x] Post-Design Constitution Check: PASS (with documented justification)
- [x] All NEEDS CLARIFICATION resolved
- [x] Complexity deviations documented

---

_Based on Constitution v2.1.1 - See `/memory/constitution.md`_
