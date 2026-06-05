# Build A — Curation Handoff Agent (Auto-Push) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `curation_handoff` terminal agent so a flow or batch run that ends on it automatically materializes curator review session(s) on completion (one per adapter), owned by the runner — no manual "Review & Curate" click.

**Architecture:** A new deterministic terminal agent (modeled on `curation_prep`) whose flow tool runs the existing two-stage prep+pipeline inline (`run_curation_prep` → per-adapter `bootstrap_document_session`, forced `SYNC` so we get session ids) via one new service function `run_flow_curation_handoff`. The flow emits a new `CURATION_HANDOFF_READY` terminal event carrying the created session ids; batch validation accepts the new terminal; the batch processor treats "sessions created" as success (instead of requiring `FILE_READY`) and records the ids on a new `BatchDocument.review_session_ids` column. Sessions are stamped with both `created_by_id` and `assigned_curator_id` = the runner, so they land in the runner's "my inventory" (ALL-557). File-output batch flows are unaffected (the processor change is additive).

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / Alembic / pytest (pytest-asyncio `asyncio_mode=auto`). Backend only.

**Design decisions locked (from the spec + gpt-5.5 review):**
- Capability token: **`curation_handoff`** (no enum/allow-list exists — any string works; flows through `agent_loader.py:271` → `registry_builder.py:114` → `AGENT_REGISTRY` → `has_batch_capability`).
- Owner = runner: stamp **both** `created_by_id` and `assigned_curator_id` (the curator-queue filter keys on `assigned_curator_id`, `session_queries.py:124`).
- Must call `run_post_curation_pipeline(execution_mode=PipelineExecutionMode.SYNC)` — `AUTO`/`ASYNC` return `session_id=None`.
- New event type `CURATION_HANDOFF_READY` (events are plain string-typed dicts; no enum to extend).
- Do **not** overload `BatchDocument.result_file_path` (parsed as `/api/files/{uuid}/download` in `api/batch.py:296`) — add a new column.

---

## File Structure

**Create:**
- `config/agents/curation_handoff/agent.yaml` — the new terminal agent definition (auto-discovered by the legacy-dir loader).
- `config/agents/curation_handoff/prompt.yaml` — minimal base prompt (agent is deterministic; the executor special-cases it).
- `backend/alembic/versions/<rev>_add_review_session_ids_to_batch_documents.py` — migration for the new column.
- `backend/tests/unit/lib/curation_workspace/test_flow_curation_handoff.py` — unit tests for the new service.

**Modify:**
- `backend/src/lib/curation_workspace/bootstrap_service.py` — add `run_flow_curation_handoff(...)` service (mirrors `prepare_chat_curation_sessions`, lines 92-136).
- `backend/src/lib/batch/validation.py:58-66` — accept `curation_handoff` as a legal exit capability.
- `backend/src/models/sql/batch.py:77-111` — add `review_session_ids` column to `BatchDocument`.
- `backend/src/lib/flows/executor.py` — add `CURATION_HANDOFF_AGENT_ID` constant + the deterministic `curation_handoff` flow tool (model on the `curation_prep` tool at `:2065-2115`) + `CURATION_HANDOFF_READY` terminal handling (model on FILE_READY at `:2927-2962`) + surface session ids on `FLOW_FINISHED` (`:2997-3012`).
- `backend/src/lib/batch/processor.py` — capture `CURATION_HANDOFF_READY` in `_execute_flow_for_document` (`:332-368` area), and relax the `FILE_READY` hard-fail in `_process_single_document` (`:226-239`) to accept curation-ready and store the ids.
- `backend/tests/unit/lib/batch/test_flow_validation.py` — add curation_handoff exit-node tests.
- `backend/tests/unit/lib/batch/test_processor.py` — add curation-ready success test.
- `backend/tests/unit/lib/agent_studio/test_batch_capabilities.py` — assert the new agent's capability.

---

## Task 1: New `curation_handoff` agent definition + capability registration

**Files:**
- Create: `config/agents/curation_handoff/agent.yaml`
- Create: `config/agents/curation_handoff/prompt.yaml`
- Test: `backend/tests/unit/lib/agent_studio/test_batch_capabilities.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/unit/lib/agent_studio/test_batch_capabilities.py`:

```python
def test_curation_handoff_agent_has_curation_handoff_capability():
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

    agent = AGENT_REGISTRY.get("curation_handoff")
    assert agent is not None, "curation_handoff agent must be registered"
    assert "curation_handoff" in agent.get("batch_capabilities", [])
    # terminal, not supervisor-routable (AGENT_REGISTRY key is "supervisor", not "supervisor_routing")
    assert agent.get("supervisor", {}).get("enabled") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/lib/agent_studio/test_batch_capabilities.py::test_curation_handoff_agent_has_curation_handoff_capability -v`
Expected: FAIL — `curation_handoff agent must be registered` (None).

- [ ] **Step 3: Create the agent definition**

Create `config/agents/curation_handoff/agent.yaml` (modeled on `config/agents/curation_prep/agent.yaml`, but accurately described as creating sessions, and with the new capability):

```yaml
# =============================================================================
# AGENT DEFINITION: curation_handoff
# =============================================================================
# Terminal agent for flows/batches. On completion it materializes curator
# review session(s) for the document (one per adapter) so curators find them
# already waiting in the curation inventory. Deterministic: invoked by the
# flow executor, not supervisor-routable.
# =============================================================================

agent_id: curation_handoff

name: "Curation Handoff Agent"

description: >
  Terminal agent that hands a flow's or batch's extraction results off to the
  curation interface: it runs curation prep and materializes one curator review
  session per adapter, owned by the curator who ran the flow/batch. Requires
  prior extraction output. Does not replace curator approval.

category: "Curation"
subcategory: "Handoff"

supervisor_routing:
  enabled: false

tools: []

output_schema: null

model_config:
  model: "${AGENT_CURATION_HANDOFF_MODEL:-gpt-5.4-mini}"
  temperature: 0.1
  reasoning: "medium"

requires_document: true
required_params:
  - document_id

batch_capabilities:
  - curation_handoff

frontend:
  icon: "CH"
  show_in_palette: true

group_rules_enabled: false
```

Create `config/agents/curation_handoff/prompt.yaml` (the executor runs the handoff deterministically; this prompt is a thin fallback):

```yaml
content: >
  You are the curation handoff step. The flow's prior extraction results are
  handed to the curation interface and a curator review session is created per
  adapter automatically. Confirm the handoff in one short sentence; do not
  invent data or call other tools.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec backend pytest tests/unit/lib/agent_studio/test_batch_capabilities.py::test_curation_handoff_agent_has_curation_handoff_capability -v`
Expected: PASS. (The legacy-dir loader at `agent_sources.py:375-394` auto-discovers `config/agents/curation_handoff/`; `batch_capabilities` flows to the registry via `registry_builder.py:114`.)

- [ ] **Step 5: Commit**

```bash
git add config/agents/curation_handoff/agent.yaml config/agents/curation_handoff/prompt.yaml backend/tests/unit/lib/agent_studio/test_batch_capabilities.py
git commit -m "feat(agents): add curation_handoff terminal agent definition"
```

---

## Task 2: Batch validation accepts a `curation_handoff` exit node

**Files:**
- Modify: `backend/src/lib/batch/validation.py:58-66`
- Test: `backend/tests/unit/lib/batch/test_flow_validation.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/lib/batch/test_flow_validation.py` (mirror the existing `test_valid_flow_passes` shape at `:8-30`, but end on `curation_handoff`):

```python
def _flow_ending_in(exit_agent_id: str) -> dict:
    return {
        "version": "1.0",
        "entry_node_id": "1",
        "nodes": [
            {"id": "1", "type": "agent", "data": {"agent_id": "pdf_extraction"}},
            {"id": "2", "type": "agent", "data": {"agent_id": "gene"}},
            {"id": "3", "type": "agent", "data": {"agent_id": exit_agent_id}},
        ],
        "edges": [
            {"id": "e1", "source": "1", "target": "2"},
            {"id": "e2", "source": "2", "target": "3"},
        ],
    }


def test_flow_ending_in_curation_handoff_is_valid_for_batch():
    result = validate_flow_for_batch(_flow_ending_in("curation_handoff"))
    assert result.valid is True
    assert result.errors == []


def test_flow_ending_in_chat_output_still_rejected():
    result = validate_flow_for_batch(_flow_ending_in("chat_output_formatter"))
    assert result.valid is False
    assert any("Chat Output" in e for e in result.errors)
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `docker compose exec backend pytest tests/unit/lib/batch/test_flow_validation.py -k curation_handoff -v`
Expected: FAIL — error "Flow must end with a file output agent…" (curation_handoff lacks `file_output`).

- [ ] **Step 3: Implement minimal change**

In `backend/src/lib/batch/validation.py`, change the exit-node loop (currently `:60-66`) to accept `curation_handoff`:

```python
    exit_nodes = get_exit_nodes(flow_definition)
    for node_id in exit_nodes:
        agent_id = get_node_agent_id(flow_definition, node_id)
        if agent_id:
            if has_batch_capability(agent_id, "chat_output"):
                errors.append("Flow ends with Chat Output - batch requires file output or curation handoff")
            elif not (
                has_batch_capability(agent_id, "file_output")
                or has_batch_capability(agent_id, "curation_handoff")
            ):
                errors.append(
                    "Flow must end with a file output agent (CSV, TSV, or JSON Formatter) "
                    "or the Curation Handoff agent"
                )
```

Also update the docstring rule (2) to mention curation handoff.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend pytest tests/unit/lib/batch/test_flow_validation.py -v`
Expected: PASS (both new tests + existing ones).

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/batch/validation.py backend/tests/unit/lib/batch/test_flow_validation.py
git commit -m "feat(batch): accept curation_handoff as a legal batch exit node"
```

---

## Task 3: `run_flow_curation_handoff` service (the core orchestration)

This is the heart of Build A: mirror `prepare_chat_curation_sessions` (`bootstrap_service.py:92-136`) but for the flow/batch context — runner identity, `FLOW` source, returns the created session ids.

**Files:**
- Modify: `backend/src/lib/curation_workspace/bootstrap_service.py`
- Test: `backend/tests/unit/lib/curation_workspace/test_flow_curation_handoff.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/lib/curation_workspace/test_flow_curation_handoff.py`. It monkeypatches `run_curation_prep` and `bootstrap_document_session` so we test the orchestration (per-adapter loop + runner identity), not the DB:

```python
from types import SimpleNamespace
from unittest.mock import Mock

import src.lib.curation_workspace.bootstrap_service as bootstrap_service
from src.lib.curation_workspace.bootstrap_service import run_flow_curation_handoff


async def test_run_flow_curation_handoff_creates_one_session_per_adapter(monkeypatch):
    db = Mock()
    db.in_transaction.return_value = True

    # Fake prep returns an output object with two adapters in scope.
    fake_prep_output = SimpleNamespace(adapter_keys=["gene", "gene_expression"])

    async def _fake_run_curation_prep(extraction_results, *, scope_confirmation, persistence_context=None, db=None):
        # assert FLOW source + runner identity threaded in
        assert persistence_context.source_kind.value == "flow"
        assert persistence_context.user_id == "runner-sub"
        return fake_prep_output

    bootstrap_calls = []

    async def _fake_bootstrap_document_session(document_id, request, *, current_user_id, db, manage_transaction):
        bootstrap_calls.append((request.adapter_key, request.curator_id, current_user_id))
        return SimpleNamespace(
            session=SimpleNamespace(session_id=f"session-{request.adapter_key}"),
            created=True,
        )

    monkeypatch.setattr(bootstrap_service, "run_curation_prep", _fake_run_curation_prep)
    monkeypatch.setattr(bootstrap_service, "bootstrap_document_session", _fake_bootstrap_document_session)
    # adapter scope is derived from the prep output
    monkeypatch.setattr(bootstrap_service, "_handoff_adapter_keys", lambda prep_output: prep_output.adapter_keys)

    result = await run_flow_curation_handoff(
        extraction_results=[SimpleNamespace(adapter_key="gene")],
        document_id="doc-1",
        runner_user_id="runner-sub",
        flow_run_id="run-1",
        origin_session_id="sess-1",
        conversation_summary="summary",
        db=db,
    )

    # one bootstrap call per adapter, curator_id == runner, current_user_id == runner
    assert [c[0] for c in bootstrap_calls] == ["gene", "gene_expression"]
    assert all(c[1] == "runner-sub" and c[2] == "runner-sub" for c in bootstrap_calls)
    assert result.review_session_ids == ["session-gene", "session-gene_expression"]
    assert result.adapter_keys == ["gene", "gene_expression"]
    db.commit.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/lib/curation_workspace/test_flow_curation_handoff.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_flow_curation_handoff'`.

- [ ] **Step 3: Implement the service**

Add to `backend/src/lib/curation_workspace/bootstrap_service.py` (near `prepare_chat_curation_sessions`). Reuse the existing imports already in that module (`run_curation_prep`, `bootstrap_document_session`, `CurationDocumentBootstrapRequest`, `CurationPrepPersistenceContext`, `CurationExtractionSourceKind`, `get_current_trace_id`). Add a small result dataclass and an adapter-scope helper.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class FlowCurationHandoffResult:
    review_session_ids: list[str]
    adapter_keys: list[str]


def _handoff_adapter_keys(prep_output) -> list[str]:
    """Adapter keys in scope for this handoff, in stable order.

    Mirrors how prepare_chat_curation_sessions iterates prep_response.adapter_keys.
    Derive from the persisted prep output's envelope refs' domain packs when the
    output does not carry adapter_keys directly.
    """
    keys = getattr(prep_output, "adapter_keys", None)
    if keys:
        return list(dict.fromkeys(keys))
    # Fallback: derive from envelope_refs' domain_pack_id (one session per pack).
    seen: list[str] = []
    for ref in getattr(prep_output, "envelope_refs", []):
        pack = getattr(ref, "domain_pack_id", None)
        if pack and pack not in seen:
            seen.append(pack)
    return seen


async def run_flow_curation_handoff(
    *,
    extraction_results,
    document_id: str,
    runner_user_id: str,
    flow_run_id: str | None,
    origin_session_id: str | None,
    conversation_summary: str | None,
    db,
) -> FlowCurationHandoffResult:
    """Flow/batch curation handoff: run prep, then one review session per adapter.

    The runner (the curator who launched the flow/batch) owns each session:
    created_by_id AND assigned_curator_id are stamped with runner_user_id so the
    sessions appear in their "my inventory" (curator filter keys on
    assigned_curator_id, session_queries.py:124). Forces SYNC inside
    bootstrap_document_session so each session id is returned inline.
    """
    try:
        prep_output = await run_curation_prep(
            extraction_results,
            scope_confirmation=_build_flow_scope_confirmation(extraction_results),
            persistence_context=CurationPrepPersistenceContext(
                document_id=document_id,
                source_kind=CurationExtractionSourceKind.FLOW,
                origin_session_id=origin_session_id,
                trace_id=get_current_trace_id(),
                flow_run_id=flow_run_id,
                user_id=runner_user_id,
                conversation_summary=conversation_summary,
            ),
            db=db,
        )

        review_session_ids: list[str] = []
        adapter_keys = _handoff_adapter_keys(prep_output)
        for adapter_key in adapter_keys:
            bootstrap_response = await bootstrap_document_session(
                document_id,
                CurationDocumentBootstrapRequest(
                    adapter_key=adapter_key,
                    origin_session_id=origin_session_id,
                    curator_id=runner_user_id,  # -> assigned_curator_id
                ),
                current_user_id=runner_user_id,  # -> created_by_id
                db=db,
                manage_transaction=False,
            )
            review_session_ids.append(bootstrap_response.session.session_id)

        db.commit()
        return FlowCurationHandoffResult(
            review_session_ids=review_session_ids,
            adapter_keys=adapter_keys,
        )
    except Exception:
        if db.in_transaction():
            db.rollback()
        raise
```

Notes for the implementer:
- `_build_flow_scope_confirmation` already exists in `executor.py`; if it is not importable from `bootstrap_service.py`, move it (or a copy) into `curation_prep_service.py` and import from there — it takes `extraction_results` and returns a `CurationPrepScopeConfirmation`. Keep one implementation (DRY).
- `CurationDocumentBootstrapRequest` already has `curator_id` (the chat bootstrap path reads `request.curator_id` → `assigned_curator_id`, `bootstrap_service.py:178-185`). Verify the field exists; if not, add it (nullable str) and thread it into the pipeline request's `assigned_curator_id`.
- `bootstrap_document_session` already forces `PipelineExecutionMode.SYNC` internally (`bootstrap_service.py:167-205`) and raises if `session_id` is None — so we get ids inline and fail loudly on async leakage.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec backend pytest tests/unit/lib/curation_workspace/test_flow_curation_handoff.py -v`
Expected: PASS.

- [ ] **Step 5: Add the ownership test**

Append a test asserting both owner fields reach the bootstrap request (already covered by `curator_id == runner` + `current_user_id == runner` in Step 1). Add one more asserting rollback on failure:

```python
async def test_run_flow_curation_handoff_rolls_back_on_failure(monkeypatch):
    db = Mock(); db.in_transaction.return_value = True
    async def _boom(*a, **k): raise RuntimeError("prep failed")
    monkeypatch.setattr(bootstrap_service, "run_curation_prep", _boom)
    import pytest
    with pytest.raises(RuntimeError, match="prep failed"):
        await run_flow_curation_handoff(
            extraction_results=[], document_id="d", runner_user_id="r",
            flow_run_id=None, origin_session_id=None, conversation_summary=None, db=db,
        )
    db.rollback.assert_called_once()
```

Run: `docker compose exec backend pytest tests/unit/lib/curation_workspace/test_flow_curation_handoff.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/lib/curation_workspace/bootstrap_service.py backend/tests/unit/lib/curation_workspace/test_flow_curation_handoff.py
git commit -m "feat(curation): add run_flow_curation_handoff (per-adapter sessions, runner-owned)"
```

---

## Task 4: `BatchDocument.review_session_ids` column + migration

**Files:**
- Modify: `backend/src/models/sql/batch.py:77-111`
- Create: `backend/alembic/versions/<rev>_add_review_session_ids_to_batch_documents.py`
- Test: `backend/tests/unit/models/test_batch_document_model.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/models/test_batch_document_model.py`:

```python
from src.models.sql.batch import BatchDocument


def test_batch_document_has_review_session_ids_column():
    assert "review_session_ids" in BatchDocument.__table__.columns
    col = BatchDocument.__table__.columns["review_session_ids"]
    assert col.nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/models/test_batch_document_model.py -v`
Expected: FAIL — `review_session_ids` not in columns.

- [ ] **Step 3: Add the column to the model**

In `backend/src/models/sql/batch.py`, inside `class BatchDocument`, after `result_file_path` (`:100`):

```python
    review_session_ids: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
```

Ensure `from sqlalchemy.dialects.postgresql import JSONB` is imported in that file (add if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec backend pytest tests/unit/models/test_batch_document_model.py -v`
Expected: PASS.

- [ ] **Step 5: Create the Alembic migration**

Find the current head:

Run: `docker compose exec backend alembic heads`
Note the revision id printed (use it as `down_revision`).

Create `backend/alembic/versions/<rev>_add_review_session_ids_to_batch_documents.py` (replace `<HEAD>` with the printed head; pick a fresh `revision` id):

```python
"""Add review_session_ids to batch_documents (curation handoff outcome).

Revision ID: a1b2c3d4e5f6
Revises: <HEAD>
Create Date: 2026-06-04 00:00:00.000000
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "<HEAD>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "batch_documents",
        sa.Column("review_session_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_documents", "review_session_ids")
```

- [ ] **Step 6: Verify the migration applies cleanly**

Run: `docker compose exec backend alembic upgrade head`
Expected: applies with no error; `alembic heads` shows your new revision as head.

- [ ] **Step 7: Commit**

```bash
git add backend/src/models/sql/batch.py backend/alembic/versions/ backend/tests/unit/models/test_batch_document_model.py
git commit -m "feat(batch): add review_session_ids column for curation-handoff outcomes"
```

---

## Task 5: Flow executor — `curation_handoff` tool + `CURATION_HANDOFF_READY` terminal event

This wires the deterministic handoff into the flow run. Model the tool on the `curation_prep` tool (`executor.py:2065-2115`) and the terminal handling on `FILE_READY` (`executor.py:2927-2962`). The executor is large and not unit-TDD-friendly; validate via the integration test in Task 7. Make each change small and grep-verifiable.

**Files:**
- Modify: `backend/src/lib/flows/executor.py`

- [ ] **Step 1: Add the agent-id constant**

Find the existing `CURATION_PREP_AGENT_ID` constant in `executor.py` and add beside it:

```python
CURATION_HANDOFF_AGENT_ID = "curation_handoff"
CURATION_HANDOFF_READY_EVENT = "CURATION_HANDOFF_READY"
```

- [ ] **Step 2: Add the deterministic handoff tool**

In the tool-building block where `if agent_id == CURATION_PREP_AGENT_ID:` is handled (`:2065`), add a sibling branch `elif agent_id == CURATION_HANDOFF_AGENT_ID:` that builds a tool which calls the new service and stashes the created session ids into the flow execution state so the terminal event can read them:

```python
        elif agent_id == CURATION_HANDOFF_AGENT_ID:
            def _make_curation_handoff_tool():
                @function_tool(name_override=tool_name, description_override=tool_description)
                async def _curation_handoff_tool(query: str) -> str:
                    _ = query
                    if not document_id or not user_id or not session_id:
                        raise RuntimeError(
                            "Curation handoff flow steps require document_id, user_id, and session_id."
                        )
                    extraction_results = _build_flow_prep_extraction_results(
                        completed_steps=execution_state["completed_steps"],
                        document_id=document_id,
                        user_id=user_id,
                        session_id=session_id,
                        flow_run_id=flow_run_id,
                        conversation_summary=flow_conversation_summary,
                    )
                    if not extraction_results:
                        raise RuntimeError(
                            "Curation handoff flow steps require at least one upstream extraction envelope."
                        )
                    with get_curation_db_session() as handoff_db:   # see Step 3
                        handoff = await run_flow_curation_handoff(
                            extraction_results=extraction_results,
                            document_id=document_id,
                            runner_user_id=str(user_id),
                            flow_run_id=flow_run_id,
                            origin_session_id=session_id,
                            conversation_summary=flow_conversation_summary,
                            db=handoff_db,
                        )
                    execution_state["curation_handoff"] = {
                        "review_session_ids": handoff.review_session_ids,
                        "adapter_keys": handoff.adapter_keys,
                    }
                    return json.dumps({
                        "review_session_ids": handoff.review_session_ids,
                        "adapter_keys": handoff.adapter_keys,
                    })
                return _curation_handoff_tool

            raw_streaming_tool = _make_curation_handoff_tool()
```

Add `from src.lib.curation_workspace.bootstrap_service import run_flow_curation_handoff` to the executor imports.

- [ ] **Step 3: Provide the DB session helper**

`run_flow_curation_handoff` needs a real `Session` (it calls `bootstrap_document_session`, which requires one and commits). Use the project's session factory the same way background curation work does. Find how `run_post_curation_pipeline`'s async path / `run_curation_prep` obtains a session when `db is None` (search `curation_prep_service.py` / `pipeline.py` for `SessionLocal` / `get_db` / a `session_scope` context manager) and reuse that exact helper as `get_curation_db_session()`. Do **not** invent a new session factory — import the existing one (e.g. `from src.models.sql.database import SessionLocal` then `with SessionLocal() as handoff_db:` if that is the established pattern). Verify by grepping for `SessionLocal(` usages in the curation_workspace lib.

- [ ] **Step 4: Emit the terminal event**

The handoff agent is a flow terminal. After its step completes, emit a `CURATION_HANDOFF_READY` event and terminate the flow, mirroring the `FILE_READY` terminal branch (`:2927-2962`) but reading from `execution_state["curation_handoff"]` instead of persisting file candidates. In the streamed-event loop, add handling so that when the curation_handoff step finishes (or its tool returns), the executor yields:

```python
            handoff_state = flow_execution_state.get("curation_handoff")
            if handoff_state and not curation_handoff_emitted:
                curation_handoff_emitted = True
                yield {
                    "type": CURATION_HANDOFF_READY_EVENT,
                    "timestamp": _now_iso(),
                    "details": {
                        "review_session_ids": handoff_state["review_session_ids"],
                        "adapter_keys": handoff_state["adapter_keys"],
                        "document_id": document_id,
                    },
                }
                break
```

Place this next to the existing `if event_type in {"FILE_READY", "CHAT_OUTPUT_READY"}:` terminal block so the curation handoff terminates the run cleanly (no `FILE_READY` is produced because the agent has no `save_*_file` tool). Do **not** route through `_persist_flow_extraction_candidates_or_build_error` — the handoff already materialized sessions.

- [ ] **Step 5: Surface session ids on `FLOW_FINISHED`**

In the `FLOW_FINISHED` event dict (`:2997-3012`), add the handoff ids so downstream consumers (and tests) can read them off the final event too:

```python
            "review_session_ids": (flow_execution_state.get("curation_handoff") or {}).get("review_session_ids", []),
```

- [ ] **Step 6: Manual smoke (no automated test here — covered by Task 7)**

Grep to confirm the wiring is internally consistent:

Run: `grep -n "CURATION_HANDOFF" backend/src/lib/flows/executor.py`
Expected: the constant, the tool branch, the terminal emit, and the FLOW_FINISHED field all present.

- [ ] **Step 7: Commit**

```bash
git add backend/src/lib/flows/executor.py
git commit -m "feat(flows): wire curation_handoff terminal agent + CURATION_HANDOFF_READY event"
```

---

## Task 6: Batch processor accepts curation-ready as success

**Files:**
- Modify: `backend/src/lib/batch/processor.py` (`_execute_flow_for_document` ~`:313-404`; `_process_single_document` ~`:216-239`)
- Test: `backend/tests/unit/lib/batch/test_processor.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/unit/lib/batch/test_processor.py` (mirror the `test_batch_processor_marks_failed_when_no_file_ready` pattern at `:38-67`):

```python
def test_batch_processor_succeeds_on_curation_handoff(monkeypatch):
    db = Mock()
    batch, batch_doc, flow = _build_batch_context()

    async def _fake_execute(**_kwargs):
        # New return contract: (result_file_path, review_session_ids)
        return (None, ["session-gene", "session-gene_expression"])

    monkeypatch.setattr(processor, "_execute_flow_for_document", _fake_execute)
    monkeypatch.setattr(processor, "get_batch_broadcaster",
                        lambda: SimpleNamespace(publish_sync=lambda _b, e: None))

    processor._process_single_document(
        db=db, batch=batch, batch_doc=batch_doc, flow=flow, cognito_sub="auth-sub",
    )

    assert batch_doc.status == BatchDocumentStatus.COMPLETED
    assert batch_doc.review_session_ids == ["session-gene", "session-gene_expression"]
    assert batch_doc.result_file_path is None
    assert batch.completed_documents == 1
```

Keep the existing `test_batch_processor_marks_failed_when_no_file_ready`, but update its fake to return `(None, [])` (no file, no sessions → still a hard failure).

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/lib/batch/test_processor.py -k curation_handoff -v`
Expected: FAIL (current `_execute_flow_for_document` returns a single value / hard-fails without FILE_READY).

- [ ] **Step 3: Capture the handoff event in `_execute_flow_for_document`**

Change `_execute_flow_for_document` to also capture `CURATION_HANDOFF_READY` and return a tuple. In the `async for event in execute_flow(...)` loop, beside the `if event_type == "FILE_READY":` block (`:332`), add:

```python
                elif event_type == "CURATION_HANDOFF_READY":
                    details = event.get("details")
                    if isinstance(details, dict):
                        review_session_ids = list(details.get("review_session_ids") or [])
                        enriched_event = _enrich_event_for_batch(event, batch_id, document_id, session_id)
                        broadcaster.publish_sync(batch_uuid, enriched_event)
```

Initialize `review_session_ids: list[str] = []` at the top of the function (next to `result_file_path = None`) and change the function's return to `return result_file_path, review_session_ids` (update the signature's return type hint to `tuple[str | None, list[str]]`).

- [ ] **Step 4: Relax the success gate in `_process_single_document`**

Where the caller does `result_file_path = asyncio.run(_execute_flow_for_document(...))` (`:216-224`) and then `if not result_file_path: raise RuntimeError(...)` (`:226-228`), change to:

```python
        result_file_path, review_session_ids = asyncio.run(
            _execute_flow_for_document(
                flow=flow, document_id=document_id, cognito_sub=cognito_sub,
                batch_id=str(batch.id), db_user_id=batch.user_id,
            )
        )

        if not result_file_path and not review_session_ids:
            raise RuntimeError("Flow completed without FILE_READY or curation handoff output")
```

And where it marks the document COMPLETED (`:233-239`), also store the session ids:

```python
        batch_doc.status = BatchDocumentStatus.COMPLETED
        batch_doc.result_file_path = result_file_path
        batch_doc.review_session_ids = review_session_ids or None
        batch_doc.processing_time_ms = processing_time_ms
        batch_doc.processed_at = datetime.now(timezone.utc)
        batch.completed_documents += 1
        db.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose exec backend pytest tests/unit/lib/batch/test_processor.py -v`
Expected: PASS (the new success test, the updated no-output failure test, and existing tests). Search for any other caller of `_execute_flow_for_document` and update it to the tuple return:

Run: `grep -rn "_execute_flow_for_document" backend/src`
Expected: only `processor.py`; if other callers exist, update them.

- [ ] **Step 6: Commit**

```bash
git add backend/src/lib/batch/processor.py backend/tests/unit/lib/batch/test_processor.py
git commit -m "feat(batch): treat curation-handoff sessions as a successful batch outcome"
```

---

## Task 7: Integration test (end-to-end auto-push)

**Files:**
- Create: `backend/tests/integration/test_curation_handoff_batch.py`

> Integration tests MUST run via `docker-compose.test.yml` (isolated postgres-test/weaviate-test/redis-test with real auth config). NEVER run inside the live backend container (`DEV_MODE=true` bypasses auth → false passes). See project `CLAUDE.local.md`.

- [ ] **Step 1: Write the integration test**

Create `backend/tests/integration/test_curation_handoff_batch.py`. Follow the structure of existing integration tests in `backend/tests/integration/` (reuse their fixtures for a seeded document + flow + user). The test:

```python
async def test_batch_flow_ending_in_curation_handoff_creates_owned_sessions(
    integration_db, seeded_pdf_document, batch_user,
):
    # 1. Build a flow: pdf_extraction -> gene -> curation_handoff
    flow = create_flow_ending_in(integration_db, exit_agent_id="curation_handoff")

    # 2. Validation accepts it
    from src.lib.batch.validation import validate_flow_for_batch
    assert validate_flow_for_batch(flow.definition).valid is True

    # 3. Run a one-document batch for batch_user
    batch = run_batch(integration_db, flow=flow, documents=[seeded_pdf_document], user=batch_user)

    # 4. Batch document completed with session ids, no file
    batch_doc = get_batch_documents(integration_db, batch)[0]
    assert batch_doc.status.name == "COMPLETED"
    assert batch_doc.review_session_ids, "handoff should record review session ids"
    assert batch_doc.result_file_path is None

    # 5. A review session exists, owned by the runner (my-inventory works)
    from src.lib.curation_workspace.session_queries import list_review_sessions
    sessions = list_review_sessions(integration_db, curator_ids=[batch_user.auth_sub])
    assert any(s.session_id in batch_doc.review_session_ids for s in sessions.sessions)
    # candidates were materialized
    assert all_sessions_have_candidates(integration_db, batch_doc.review_session_ids)
```

Add a second test `test_multi_adapter_flow_creates_one_session_per_adapter` (flow with two extractor adapters → assert `len(batch_doc.review_session_ids) == 2`), and `test_rerun_does_not_duplicate_sessions` (run the same batch twice → same session ids, no duplicates — relies on `find_reusable_prepared_session`).

- [ ] **Step 2: Run the integration tests**

Run: `docker compose -f docker-compose.test.yml run --rm backend-integration-tests`
Expected: the three new tests PASS (plus existing suite green).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_curation_handoff_batch.py
git commit -m "test(batch): end-to-end curation handoff auto-push integration tests"
```

---

## Task 8: Full-suite regression + manual smoke

- [ ] **Step 1: Run the affected unit suites**

Run: `docker compose exec backend pytest tests/unit/lib/batch tests/unit/lib/curation_workspace tests/unit/lib/agent_studio tests/unit/models -v`
Expected: all PASS.

- [ ] **Step 2: Run integration + persistence suites**

Run: `docker compose -f docker-compose.test.yml run --rm backend-integration-tests`
Run: `docker compose -f docker-compose.test.yml run --rm backend-persistence-tests`
Expected: green.

- [ ] **Step 3: Manual smoke on dev**

In Agent Studio, build a flow `pdf_extraction → gene_expression → Curation Handoff`. Confirm it passes batch validation, run a small batch, then open the curation inventory as the same user and confirm the session(s) appear under "my inventory" with candidates — without clicking "Review & Curate".

- [ ] **Step 4: Final commit / branch is ready for PR**

```bash
git log --oneline -8
```

---

## Open items to confirm with Chris before/while implementing
- **Capability/agent naming** (`curation_handoff`) — fine to lock?
- **`assigned_curator_id` = runner** (so "my inventory" works) vs. leaving unassigned and changing ALL-557's filter to `created_by_id`. Plan assumes "stamp both = runner."
- **Multi-adapter labeling** — sessions currently differ only by `adapter_key`; do we want a `notes`/`tags` default so they're easy to tell apart in inventory? (Not blocking; can default to none.)
- **Standalone (non-batch) flow runs** ending in `curation_handoff` also create sessions (the tool runs regardless of batch). Confirm that's desired (it completes the previously-incomplete flow path).

---

## gpt-5.5 Review Corrections (fold in before implementing)

Verdict: **Sound-with-corrections.** Apply these:

1. **Task 3 — per-adapter prep loop (most important).** `run_curation_prep` requires **exactly one adapter key** (`curation_prep_service.py:464`) and returns `CurationPrepAgentOutput` with **`envelope_refs`, not `adapter_keys`**. So the service must **loop adapters and call `run_curation_prep` once per adapter** (like chat at `curation_prep_invocation.py:90`), not call it once and read `prep_output.adapter_keys`. Replace `_handoff_adapter_keys(prep_output)` (the `domain_pack_id → adapter_key` fallback is unsafe — packs have their own ids) with adapter keys **derived from the scoped extraction records** (each `CurationExtractionResultRecord.adapter_key`). Corrected core:

   ```python
   adapter_keys = sorted({r.adapter_key for r in extraction_results if r.adapter_key})
   review_session_ids: list[str] = []
   for adapter_key in adapter_keys:
       adapter_records = [r for r in extraction_results if r.adapter_key == adapter_key]
       await run_curation_prep(
           adapter_records,
           scope_confirmation=_build_flow_scope_confirmation(adapter_records),
           persistence_context=CurationPrepPersistenceContext(
               document_id=document_id, source_kind=CurationExtractionSourceKind.FLOW,
               origin_session_id=origin_session_id, trace_id=get_current_trace_id(),
               flow_run_id=flow_run_id, user_id=runner_user_id, conversation_summary=conversation_summary,
           ),
           db=db,
       )
       bootstrap_response = await bootstrap_document_session(
           document_id,
           CurationDocumentBootstrapRequest(adapter_key=adapter_key, origin_session_id=origin_session_id, curator_id=runner_user_id),
           current_user_id=runner_user_id, db=db, manage_transaction=False,
       )
       review_session_ids.append(bootstrap_response.session.session_id)
   db.commit()
   ```
   Update the Task 3 test accordingly (the fake `run_curation_prep` no longer needs to return `adapter_keys`; assert one prep + one bootstrap per distinct record adapter). Confirmed good: `CurationDocumentBootstrapRequest` **does** have `curator_id` (`curation_workspace.py:1846`); `bootstrap_document_session` already forces `PipelineExecutionMode.SYNC` and raises if no `session_id` (`bootstrap_service.py:168,191,196`).

2. **Task 3 — imports.** `bootstrap_service.py` does **not** already import `run_curation_prep`, `CurationPrepPersistenceContext`, `CurationExtractionSourceKind`, `get_current_trace_id`, or `_build_flow_scope_confirmation`. Add them (and ensure `_build_flow_scope_confirmation` is importable — move it to `curation_prep_service.py` if it currently lives in `executor.py`).

3. **Task 5 Step 3 — DB session.** `get_curation_db_session()` does not exist. Use **`SessionLocal`** (already imported in the executor at `executor.py:80`): `with SessionLocal() as handoff_db:`. (Or follow the processor contextmanager at `processor.py:87`.)

4. **Task 5 Step 4 — prevent fallback persistence.** A plain `break` is not enough: the executor later persists extraction candidates when `extraction_persisted` is false (`executor.py:2979`). Set a handoff-completed flag (or set the equivalent `extraction_persisted = True`) so the post-loop fallback persistence does **not** run for the handoff path.

5. **New step — API visibility of `review_session_ids`.** Adding the column isn't enough for the UI: also add the field to `BatchDocumentResponse` (`backend/src/schemas/batch.py:19`), populate it in `BatchService.batch_to_response()` (`backend/src/lib/batch/service.py:271`), and include it in the batch-status SSE (`backend/src/api/batch.py:518`). Add this as a step in Task 4.

6. **Minor:** "AUTO returns `session_id=None`" is overbroad — `AUTO` is sync for small payloads; only `ASYNC` guarantees no id. Forcing `SYNC` remains correct. Loader-path prose: discovery is `backend/src/lib/config/agent_sources.py:375` (not under `lib/agent_studio`) — the plan's path was already correct; just don't confuse the prose.

## Self-Review (completed)

- **Spec coverage:** new agent (T1) ✓; batch validation (T2) ✓; per-adapter sessions + owner stamping + SYNC (T3) ✓; non-file success signal + storage (T4, T6) ✓; flow-end trigger + event (T5) ✓; identity = runner both fields (T3) ✓; integration incl. multi-adapter + re-run dedupe (T7) ✓. Out-of-scope items (curation_prep untouched, submission gate, file-output flows) are preserved (T2/T6 are additive).
- **Placeholder scan:** no TBD/"add error handling"; the two genuinely environment-specific lookups (current alembic head in T4-S5; the existing session factory in T5-S3) are explicit verification steps, not hand-waves.
- **Type consistency:** `_execute_flow_for_document` returns `tuple[str | None, list[str]]` in T6 and is consumed as a tuple in `_process_single_document` (T6-S4) and the test (T6-S1); `run_flow_curation_handoff` returns `FlowCurationHandoffResult(review_session_ids, adapter_keys)` used consistently in T3 and read in T5; the event key `review_session_ids` is the same in T5 (emit), T6 (capture), and T7 (assert).
