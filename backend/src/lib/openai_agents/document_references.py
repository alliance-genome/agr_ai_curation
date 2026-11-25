"""Durable document-local references backed by canonical source/read history.

Indexes are existing ingestion positions, never allocated aliases. A conversation
pins its source generation and each read's exact span definitions in the canonical
history store. Compaction and worker restarts cannot change those definitions.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import select, text

from agr_ai_curation_runtime.document_errors import DocumentFilesChanged, DocumentReadError
from agr_ai_curation_runtime.document_references import DocumentSelection
from agr_ai_curation_runtime.evidence_spans import resolve_evidence_span_id
from src.lib.context import get_current_session_id, get_current_trace_id, get_current_user_id
from src.lib.document_files.service import FileRangeMissingError
from src.lib.document_files.files_read import FILES_READ_KIND
from src.lib.document_files.readable import (
    ReadableFiles,
    ReadableFilesError,
    readable_files_in_session,
    resolve_readable_files,
)
from src.lib.weaviate_client.chunks import get_chunk_by_index
from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from .run_history import ACTIVE_HISTORY, HistoryScope, HistoryStore, exact_json, record_runtime_payload
from .tool_surface import canonical_tool_name

_SOURCE_KIND = "document_source_binding"
_SPAN_KIND = "document_span_binding"
_WINDOW_KIND = "document_span_window"
logger = logging.getLogger(__name__)
_ACTIVE_SOURCE: ContextVar["SourceBinding | None"] = ContextVar("document_source_binding", default=None)


class DocumentReferenceError(ValueError):
    """Structural source/selection failure; no scientific judgment is involved."""


class UnissuedDocumentSelectionError(DocumentReferenceError):
    """The agent selected a chunk or span window it has not read in this scope."""

    def __init__(self, selection: DocumentSelection, message: str):
        self.selection = selection
        super().__init__(message)


class UnreadableDocumentSelectionError(DocumentReferenceError):
    """The agent selected a chunk outside every file the run can read now."""

    def __init__(self, selection: DocumentSelection, readable: ReadableFiles):
        self.selection = selection
        self.readable = readable
        super().__init__("Selected chunk is not part of a readable file")


def _read_bindings(store: HistoryStore, scope: HistoryScope, document_id: str, *, db=None) -> list[dict]:
    # Only backend-owned source bindings cross specialist lineage boundaries.
    # Arbitrary history payloads still use HistoryStore's stricter access policy.
    condition = AgentHistoryEvent.owner_id == scope.owner_id
    condition &= (AgentHistoryEvent.session_id == scope.session_id if scope.session_id
                  else AgentHistoryEvent.run_id == scope.run_id)
    query = select(AgentHistoryEvent, AgentHistoryPayload.content).join(AgentHistoryPayload).where(
        condition, AgentHistoryEvent.kind.in_([_SOURCE_KIND, _SPAN_KIND, _WINDOW_KIND, FILES_READ_KIND])
    ).order_by(AgentHistoryEvent.sequence)
    if db is None:
        with store.session_factory() as reader:
            rows = reader.execute(query).all()
    else:
        rows = db.execute(query).all()
    result = []
    for event, content in rows:
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != event.payload_sha256:
            raise DocumentReferenceError("Source history integrity check failed")
        payload = json.loads(content)
        if payload.get("document_id") == document_id:
            # Files-read rows are per invocation (ALL-1460); the others are per session.
            invocation = {"invocation_id": event.invocation_id} if event.kind == FILES_READ_KIND else {}
            result.append({"kind": event.kind, **payload, **invocation})
    return result


def _span_definition(chunk: dict, spans: list[dict]) -> dict:
    index = chunk["chunk_index"]
    if type(index) is not int or index < 0:
        raise DocumentReferenceError("Stored chunk index is invalid")
    definitions = []
    for source_span in spans:
        verified = resolve_evidence_span_id(span_id=source_span["span_id"],
            chunk_text=chunk["text"], expected_chunk_id=chunk["id"])
        if (verified.span_index != source_span["span_index"]
                or verified.char_start != source_span["char_start"]
                or verified.char_end != source_span["char_end"]
                or verified.text != source_span["text"]):
            raise DocumentReferenceError("Issued span definition changed from its canonical identity")
        definitions.append({k: source_span[k] for k in ("span_id", "span_index", "spanizer_version")})
    return {"chunk_index": index, "chunk_id": chunk["id"],
            "content_sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(),
            "spans": definitions}


@dataclass
class SourceBinding:
    document_id: str
    user_id: str
    generation: str
    history_scope: HistoryScope
    store: HistoryStore
    records: list[dict]
    # The files this tool call may read, resolved with the generation check.
    readable: ReadableFiles
    staged_updates: list[tuple[dict, dict, dict]] = field(default_factory=list)
    # Chunk indexes whose text this call showed or used (reads, search hits,
    # evidence selections), all through ``note_shown``. The end-of-call check needs
    # to know whether any came from a file that stopped being readable, and their
    # files become the call's files-read row once the call stands (``record_files_read``).
    shown_chunk_indexes: set[int] = field(default_factory=set)

    def stage_update(self, target: dict, replacement: dict) -> None:
        self.staged_updates.append((target, deepcopy(target), deepcopy(replacement)))

    def commit_updates(self) -> None:
        # Run only after the final generation check, without an intervening await.
        # A stale read must not partially mutate an existing evidence record.
        if any(target != original for target, original, _ in self.staged_updates):
            raise DocumentReferenceError("Evidence record changed during source verification")
        for target, _, replacement in self.staged_updates:
            target.clear()
            target.update(replacement)

    def append(self, kind: str, payload: dict, *, db=None) -> None:
        value = {"document_id": self.document_id, "generation": self.generation, **payload}
        # Required provenance fails closed; it is not optional telemetry.
        if db is None:
            self.store.append(self.history_scope, kind, exact_json(value))
        else:
            self.store.append_in_transaction(db, self.history_scope, kind, exact_json(value))
        self.records.append({"kind": kind, **value})

    def pin_generation(self, *, db=None) -> None:
        if any(record["generation"] != self.generation for record in self.records):
            raise DocumentReferenceError("Document source changed; start a new conversation")
        if not self.records:
            self.append(_SOURCE_KIND, {}, db=db)

    def retain(self, reads: list[tuple[dict, list[dict], list[int]]]) -> None:
        """Issue the shown spans of every chunk one read displayed, atomically.

        ``reads`` holds ``(chunk, spans, issued_indexes)`` per displayed chunk.
        One transaction and one lock cover the whole read, so a section read
        costs one binding scan, and a failure issues nothing it displayed.
        """
        definitions = {}
        for chunk, spans, issued_indexes in reads:
            definition = _span_definition(chunk, spans)
            index = definition["chunk_index"]
            if index in definitions and definitions[index][0] != definition:
                raise DocumentReferenceError("One read showed two definitions for one chunk")
            if not set(issued_indexes) <= {s["span_index"] for s in definition["spans"]}:
                raise DocumentReferenceError("Issued window contains an undefined span")
            previous = definitions.get(index, (definition, set()))[1]
            definitions[index] = (definition, previous | set(issued_indexes))
        self.note_shown(definitions)
        with self._locked_transaction() as db:
            defined = {}
            issued = {}
            for record in self.records:
                if record["kind"] == _SPAN_KIND:
                    defined.setdefault(record["chunk_index"], []).append(record)
                elif record["kind"] == _WINDOW_KIND:
                    issued.setdefault(record["chunk_index"], set()).update(record["issued_indexes"])
            for index, (definition, issued_indexes) in definitions.items():
                existing = defined.get(index, [])
                for record in existing:
                    if any(record[key] != value for key, value in definition.items()):
                        raise DocumentReferenceError("Previously issued source/span definition changed")
                if not existing:
                    self.append(_SPAN_KIND, definition, db=db)
                added = issued_indexes - issued.get(index, set())
                if added:
                    # Retain each definition once; later reads add only ordinal coverage.
                    self.append(_WINDOW_KIND, {"chunk_index": index, "issued_indexes": sorted(added)}, db=db)

    @contextmanager
    def _locked_transaction(self):
        """One transaction under the reference lock, with the bindings re-read and pinned."""
        with self.store.session_factory() as db, db.begin():
            # All reference issuers use this transaction lock. It protects a
            # definition, not model context size, and never spans async tool I/O.
            if db.bind.dialect.name == "postgresql":
                lock_key = exact_json([self.user_id, self.history_scope.session_id or self.history_scope.run_id, self.document_id])
                db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock_key})
            self.records = _read_bindings(self.store, self.history_scope, self.document_id, db=db)
            self.pin_generation(db=db)
            yield db

    def note_shown(self, chunk_indexes) -> None:
        """Pieces whose text this call shows the model: reads, search hits, evidence selections.

        Search snippets are not evidence, so nothing becomes selectable; the files
        whose text the model saw still count as read (ALL-1460, Q2).
        """
        self.shown_chunk_indexes.update(int(index) for index in chunk_indexes)

    def record_files_read(self) -> None:
        """Write the files-read row for the pieces this call showed, once the call stands.

        Runs after the end-of-call check, so a call that is withheld (files changed),
        fails, or ends the run leaves no row. Span issuance (``retain``) stays immediate.
        """
        if not self.shown_chunk_indexes:
            return
        # Files-read rows are append-only, so a file already in this invocation's
        # records (re-read under the lock by ``retain``, or loaded with the binding)
        # stays recorded: skip the locked scan when every shown file is there.
        if {self._shown_file(index).file_id for index in self.shown_chunk_indexes} <= self._files_recorded():
            return
        with self._locked_transaction() as db:
            self._record_files_read(sorted(self.shown_chunk_indexes), db=db)

    def _shown_file(self, index: int):
        file = self.readable.file_for(index)
        if file is None:
            raise DocumentReferenceError("A shown chunk is not part of a readable file")
        return file

    def _files_recorded(self) -> set[str]:
        """The files this invocation's files-read records name, from ``self.records``."""
        invocation_id = self.history_scope.invocation_id
        return {
            entry["file_id"]
            for record in self.records
            if record["kind"] == FILES_READ_KIND and record["invocation_id"] == invocation_id
            for entry in record["files"]
        }

    def _record_files_read(self, chunk_indexes, *, db) -> None:
        """Record the files these pieces belong to that the invocation had not been shown.

        One row per newly shown set of files (ALL-1460, Q8), so a long run writes at
        most one row per file per invocation, written by ``record_files_read`` once the
        call stands. The "already shown" set comes from the bindings scanned under the
        same lock. The first read's values stand for the invocation: a file's role and
        converted text change in place only when the paper is processed again, which
        gives it a new generation that every later read in the session refuses.
        Every agent run with history enabled has its own invocation; with history
        disabled each tool call gets a fresh one, so each read then writes a row.
        """
        shown = {}
        for index in chunk_indexes:
            file = self._shown_file(index)
            shown.setdefault(file.file_id, file)
        invocation_id = self.history_scope.invocation_id
        already = self._files_recorded()
        # Each file as it is now, when it is read: a saved result keeps these values even
        # if the file is later removed or its row rewritten (ALL-1460, decision 6).
        new_files = [
            {
                "file_id": file_id,
                "file_name": file.display_name,
                "role": file.role,
                "converted_artifact_id": file.converted_artifact_id,
                "converted_artifact_sha256": file.converted_artifact_sha256,
            }
            for file_id, file in shown.items()
            if file_id not in already
        ]
        if new_files:
            self.append(FILES_READ_KIND, {"files": new_files}, db=db)
            self.records[-1]["invocation_id"] = invocation_id

    async def resolve(self, selections: list[DocumentSelection]) -> list[str]:
        if not selections:
            raise DocumentReferenceError("Select at least one issued source span")
        self.records = await asyncio.to_thread(_read_bindings, self.store, self.history_scope, self.document_id)
        self.pin_generation()
        result = []
        chunks = {}
        for selection in selections:
            records = [r for r in self.records if r["kind"] == _SPAN_KIND
                       and r["chunk_index"] == selection.chunk_index]
            if not records:
                raise UnissuedDocumentSelectionError(selection, "Read the selected chunk before recording evidence")
            record = records[-1]
            span = next((s for s in record["spans"] if s["span_index"] == selection.span_index), None)
            if span is None:
                raise DocumentReferenceError("Selected span is unavailable")
            issued = {i for r in self.records if r["kind"] == _WINDOW_KIND
                      and r["chunk_index"] == selection.chunk_index for i in r["issued_indexes"]}
            if selection.span_index not in issued:
                raise UnissuedDocumentSelectionError(selection, "Read the selected span window before recording evidence")
            # Only a piece that was read can have stopped being readable since (its
            # file removed or no longer ready); a piece never read stays "unissued".
            if not self.readable.files:
                # A completed paper always has a ready file; nothing to offer the model.
                raise DocumentReferenceError("Document has no readable file")
            if not self.readable.contains(selection.chunk_index):
                raise UnreadableDocumentSelectionError(selection, self.readable)
            if selection.chunk_index not in chunks:
                chunks[selection.chunk_index] = await get_chunk_by_index(
                    chunk_index=selection.chunk_index, user_id=self.user_id, document_id=self.document_id,
                    readable=self.readable)
            chunk = chunks[selection.chunk_index]
            if (not chunk or chunk["id"] != record["chunk_id"]
                    or hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest() != record["content_sha256"]):
                raise DocumentReferenceError("Selected source no longer matches its issued definition")
            resolve_evidence_span_id(span_id=span["span_id"], chunk_text=chunk["text"], expected_chunk_id=chunk["id"])
            if span["span_id"] not in result:
                result.append(span["span_id"])
        return result


def _generation(db, document_id: str, user_id: str, *, lock: bool = False) -> str:
    query = select(PDFDocument).join(User, User.id == PDFDocument.user_id).where(
        PDFDocument.id == UUID(document_id), User.auth_sub == user_id)
    if lock:
        query = query.with_for_update(of=PDFDocument)
    doc = db.scalar(query)
    if doc is None or doc.status != "completed" or doc.processing_completed_at is None:
        raise DocumentReferenceError("Document is unavailable or being reprocessed")
    return doc.processing_completed_at.isoformat()


def _load_source_binding(document_id: str, user_id: str, scope: HistoryScope, store: HistoryStore):
    with store.session_factory() as db, db.begin():
        # Briefly serialize first bindings with generation/status changes. Do not
        # hold a SQL row lock across asynchronous tool I/O: other application
        # endpoints can still use synchronous SQL transactions on the event loop.
        generation = _generation(db, document_id, user_id, lock=True)
        readable = readable_files_in_session(db, document_id, user_id)
        records = _read_bindings(store, scope, document_id, db=db)
        binding = SourceBinding(document_id, user_id, generation, scope, store, records, readable)
        binding.pin_generation(db=db)
        return binding


def _verify_source_generation(binding: SourceBinding) -> None:
    """The end-of-call check: the paper is the one the call read, and so is every file it showed.

    - A new generation (the paper reprocessed, deleted or not completed) is terminal.
    - Readable files only added (an add or a supplement wait finishing): the call's
      text is still right; the result stands and the next call sees the new files.
    - A file the call could read stopped being readable (removed, or no longer
      ready): terminal if the call showed text from it; otherwise the call is
      withheld with a recoverable answer (``DocumentFilesChanged``).
    """
    with SessionLocal() as db:
        if _generation(db, binding.document_id, binding.user_id) != binding.generation:
            raise DocumentReferenceError("Document changed while reading or verifying source")
        now_readable = set(readable_files_in_session(db, binding.document_id, binding.user_id).fingerprint)
    gone = [
        file for file in binding.readable.files
        if (file.file_id, file.chunk_index_start, file.chunk_index_end) not in now_readable
    ]
    if not gone:
        return
    if any(file.contains(index) for file in gone for index in binding.shown_chunk_indexes):
        raise DocumentReferenceError("A readable file changed while reading or verifying source")
    raise DocumentFilesChanged()


def _files_changed_only(binding: SourceBinding) -> None:
    """After a failed fetch: raise ``DocumentFilesChanged`` only if a readable file the
    call showed nothing from is gone; otherwise return and let the read failure stand."""
    try:
        _verify_source_generation(binding)
    except DocumentFilesChanged:
        raise
    except DocumentReferenceError:
        return


async def _finish(task):
    """Drain owned synchronous work even when its waiter is cancelled repeatedly."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    value = task.result()
    return value, cancelled


@asynccontextmanager
async def document_reference_scope(document_id: str, user_id: str, *, tool_name: str, call_id: str):
    owner, run_id, session_id = get_current_user_id(), get_current_trace_id(), get_current_session_id()
    if not owner or owner != user_id or not run_id:
        raise DocumentReadError("document_reference") from DocumentReferenceError("Missing authenticated source scope")
    token = None
    try:
        history = ACTIVE_HISTORY.get()
        if history is not None:
            history.assert_current_scope()
            scope, store = history.scope, history.store
        else:
            invocation = str(uuid4())
            scope = HistoryScope(owner, run_id, invocation, "document_reference", invocation + "/", session_id)
            store = HistoryStore()
        binding, cancelled = await _finish(asyncio.create_task(asyncio.to_thread(
            _load_source_binding, document_id, user_id, scope, store)))
        if cancelled:
            raise asyncio.CancelledError
        token = _ACTIVE_SOURCE.set(binding)
        try:
            yield binding
        except DocumentReadError:
            # A fetch can fail because its file stopped being readable during the call
            # (a removal deletes the file's row, then its pieces). The readable-files
            # comparison decides, never the error: a file gone that the call showed
            # nothing from is "files changed"; anything else stays the failed read.
            _, cancelled = await _finish(asyncio.create_task(asyncio.to_thread(_files_changed_only, binding)))
            if cancelled:
                raise asyncio.CancelledError
            raise
        # Ingestion commits processing status before changing Weaviate, then a
        # new completion timestamp. Check both sides of the read/verify operation.
        _, cancelled = await _finish(asyncio.create_task(asyncio.to_thread(_verify_source_generation, binding)))
        if cancelled:
            raise asyncio.CancelledError
        # Before the evidence update takes effect: a failed write saves no evidence.
        _, cancelled = await _finish(asyncio.create_task(asyncio.to_thread(binding.record_files_read)))
        if cancelled:
            raise asyncio.CancelledError
        binding.commit_updates()
    except DocumentFilesChanged:
        logger.info("Withheld a document tool call: the paper's readable files changed",
                    extra={"document_id": document_id, "run_id": run_id, "tool_name": tool_name, "call_id": call_id})
        # Ids only, never text: a trace ties the event to the withheld call.
        await record_runtime_payload("document_files_changed", {
            "document_id": document_id, "tool_name": tool_name, "call_id": call_id},
            tool_name=tool_name, call_id=call_id)
        raise
    except DocumentReadError:
        raise
    except Exception as exc:
        raise DocumentReadError("document_reference") from exc
    finally:
        if token is not None:
            _ACTIVE_SOURCE.reset(token)


def current_readable_files() -> ReadableFiles:
    """The readable files of the document tool call in progress."""
    binding = _ACTIVE_SOURCE.get()
    if binding is None:
        raise DocumentReferenceError("Missing source binding")
    return binding.readable


async def retain_issued_spans(reads: list[tuple[dict, list[dict], list[int]]]) -> None:
    """Issue every span one read displayed: ``(chunk, spans, issued_indexes)`` per chunk."""
    binding = _ACTIVE_SOURCE.get()
    if binding is None:
        raise DocumentReferenceError("Missing source binding")
    await asyncio.to_thread(binding.retain, reads)


async def record_files_shown(chunk_indexes: list[int]) -> None:
    """Pieces a tool showed without issuing spans (search hits, recorded quotes).

    Their files count as read when the call stands; see ``SourceBinding.record_files_read``.
    """
    binding = _ACTIVE_SOURCE.get()
    if binding is None:
        raise DocumentReferenceError("Missing source binding")
    binding.note_shown(chunk_indexes)


def stage_evidence_update(target: dict, replacement: dict) -> None:
    binding = _ACTIVE_SOURCE.get()
    if binding is None:
        raise DocumentReferenceError("Missing source binding")
    binding.stage_update(target, replacement)


async def resolve_selections(selections: list[DocumentSelection]) -> list[str]:
    binding = _ACTIVE_SOURCE.get()
    if binding is None:
        raise DocumentReferenceError("Missing source binding")
    return await binding.resolve(selections)


REFERENCE_RESULT_TOOLS = frozenset({
    "record_evidence", "list_recorded_evidence", "get_recorded_evidence",
    "attach_evidence_to_object", "detach_evidence_from_object",
    "discard_recorded_evidence", "update_recorded_evidence_metadata",
})


async def project_source_references(value, *, user_id: str, document_id: str | None = None):
    """Project structured provenance fields only; never rewrite scientific text.

    Saved canonical evidence remains readable even when its source was replaced.
    Such records have no current local selection, rather than a guessed mapping.
    """
    from src.lib.weaviate_client.chunks import get_chunk_by_id
    from agr_ai_curation_runtime.evidence_spans import parse_evidence_span_id, EvidenceSpanResolutionError
    cache = {}
    readable_by_document = {}

    async def readable_for(chunk_document_id):
        if chunk_document_id not in readable_by_document:
            try:
                readable_by_document[chunk_document_id] = await asyncio.to_thread(
                    resolve_readable_files, chunk_document_id, user_id)
            except FileRangeMissingError as exc:
                # A ready file without a range (the paper-files fill step has not run):
                # the same named failure the document tools give.
                raise DocumentReadError("document_reference") from exc
            except ReadableFilesError:
                # The piece's paper row is gone: its source is unavailable, like a missing piece.
                readable_by_document[chunk_document_id] = None
        return readable_by_document[chunk_document_id]

    async def chunk_for(chunk_id, doc_id):
        key = (chunk_id, doc_id)
        if key not in cache:
            try:
                UUID(chunk_id)
            except (ValueError, TypeError, AttributeError):
                cache[key] = None
                return None
            chunk = await get_chunk_by_id(chunk_id=chunk_id, user_id=user_id, document_id=doc_id)
            if chunk and (type(chunk.get("chunk_index")) is not int or chunk["chunk_index"] < 0
                          or not isinstance(chunk.get("document_id"), str)):
                chunk = None
            if chunk:
                # A piece outside the run's readable files is not a current source.
                readable = await readable_for(chunk["document_id"])
                if readable is None or not readable.contains(chunk["chunk_index"]):
                    chunk = None
            cache[key] = chunk
        return cache[key]

    async def walk(item, bound_doc):
        if isinstance(item, list):
            return [await walk(child, bound_doc) for child in item]
        if not isinstance(item, dict):
            return item
        doc_id = item.get("document_id") or bound_doc
        output = {key: await walk(child, doc_id) for key, child in item.items()
            if key not in {"chunk_id", "chunk_ids", "span_id", "span_ids", "source_span_ids",
                           "previous_chunk_id", "next_chunk_id", "failed_span_id"}}
        chunk_id = item.get("chunk_id")
        if chunk_id:
            chunk = await chunk_for(chunk_id, doc_id)
            if chunk:
                output["chunk_index"] = chunk["chunk_index"]
                output["document_id"] = chunk["document_id"]
            else:
                output["source_reference_status"] = "source_unavailable"
        span_ids = item.get("source_span_ids") or item.get("span_ids")
        if not span_ids and item.get("span_id"):
            span_ids = [item["span_id"]]
        if span_ids:
            selections = []
            for span_id in span_ids:
                try:
                    parsed = parse_evidence_span_id(span_id)
                    chunk = await chunk_for(parsed.chunk_id, doc_id)
                    if not chunk:
                        raise DocumentReferenceError("Historical source is unavailable")
                    span = resolve_evidence_span_id(span_id=span_id, chunk_text=chunk["text"], expected_chunk_id=chunk["id"])
                    selection = {"chunk_index": chunk["chunk_index"], "span_index": span.span_index}
                    if selection not in selections:
                        selections.append(selection)
                except (DocumentReferenceError, EvidenceSpanResolutionError, ValueError, TypeError):
                    output["source_reference_status"] = "source_unavailable"
                    selections = []
                    break
            output["selections"] = selections
            if selections:
                output["selection_instructions"] = "Read these chunks in the current conversation before re-recording evidence."
        return output

    return await walk(value, document_id)


def _source_mapping(raw):
    from .evidence_summary import coerce_tool_event_dict
    payload = coerce_tool_event_dict(raw)
    if payload is None and isinstance(raw, str):
        # The SDK serializes Python dictionary tool outputs using repr.
        import ast
        try:
            decoded = ast.literal_eval(raw)
            payload = decoded if isinstance(decoded, dict) else None
        except (ValueError, SyntaxError):
            pass
    return payload


def _refresh_document_guidance(payload, tool):
    """Replace backend-authored guidance only, never scientific text fields."""
    from .document_result_view import DOCUMENT_READ_TOOLS
    if tool not in DOCUMENT_READ_TOOLS or not isinstance(payload, dict):
        return payload
    payload = deepcopy(payload)
    if "summary" in payload and isinstance(payload["summary"], str) and any(name in payload["summary"] for name in ("span_id", "chunk_id")):
        payload["summary"] = "Use chunk_index and span_index selections with record_evidence. Read source in the current conversation before recording."
    return payload


async def project_history_source_result(result: dict, *, user_id: str) -> dict:
    """Keep archives exact while projecting known source results before paging."""
    from .document_result_view import DOCUMENT_READ_TOOLS, document_result_view
    event = result["event"]
    tool = canonical_tool_name(event.get("tool_name"))
    if tool not in DOCUMENT_READ_TOOLS | REFERENCE_RESULT_TOOLS and event.get("kind") != _SPAN_KIND:
        return result
    payload = result["payload"]
    sdk_item = event.get("kind") in {"input_item", "context_item"} and isinstance(payload, dict)
    sdk_field = ("output" if payload.get("type") == "function_call_output" else
                 "arguments" if payload.get("type") == "function_call" else None) if sdk_item else None
    outer = payload if sdk_field else None
    if sdk_field:
        payload = payload.get(sdk_field)
    if isinstance(payload, str):
        payload = _source_mapping(payload)
        if payload is None:
            payload = {"history_projection_status": "old_unstructured_source_result",
                "message": "This old source result has no safe local-reference projection. Read the document again using current tools."}
    if event.get("kind") == "tool_start" and isinstance(payload, dict):
        arguments = _source_mapping(payload.get("arguments"))
        if arguments is not None:
            payload = {**payload, "arguments": await project_source_references(arguments, user_id=user_id),
                "replay_instructions": "Historical arguments are audit data. Read source with current tools before making a new evidence selection."}
        else:
            payload = {"history_projection_status": "old_unstructured_source_arguments"}
    payload = await project_source_references(payload, user_id=user_id)
    payload = _refresh_document_guidance(payload, tool)
    if event.get("kind") == "canonical_tool_result" and tool in DOCUMENT_READ_TOOLS:
        payload, _ = document_result_view(tool, payload)
    if sdk_field and isinstance(outer, dict):
        payload = {**outer, sdk_field: exact_json(payload)}
    return {**result, "payload": payload}


# A verified record_evidence result as the model sees it: the new record's
# identity and location plus the exact quote once. The model already holds
# what it sent (entity, selections, targets); per-fragment copies of the quote
# and repeated document identity stay in the stored result.
_RECORD_EVIDENCE_MODEL_FIELDS = (
    "status", "evidence_record_id", "chunk_index", "page", "section", "subsection",
    "figure_reference", "verified_quote", "source_reference_status",
)


def _compact_reference_view(tool: str, payload: dict) -> dict:
    """Model-only shape for evidence results; stored results are unchanged."""
    if tool == "record_evidence" and payload.get("status") == "verified":
        return {key: payload[key] for key in _RECORD_EVIDENCE_MODEL_FIELDS if key in payload}
    if tool == "list_recorded_evidence" and isinstance(payload.get("evidence_records"), list):
        document_id = payload.get("document_id")
        return {**payload, "evidence_records": [
            {key: value for key, value in record.items()
             if not (key == "document_id" and value == document_id)
             and not (key == "envelope_targets" and value == [])}
            if isinstance(record, dict) else record
            for record in payload["evidence_records"]]}
    return payload


async def project_model_source_items(items: list, *, user_id: str | None, cache: dict) -> list:
    """Model-only view. SDK run items and evidence/UI consumers keep originals."""
    from copy import deepcopy
    from .document_result_view import DOCUMENT_READ_TOOLS
    if not user_id:
        return items
    tools = {item.get("call_id"): canonical_tool_name(item.get("name")) for item in items
             if isinstance(item, dict) and item.get("type") == "function_call"}
    result = deepcopy(items)
    for item in result:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        name = tools.get(item.get("call_id"))
        if name not in REFERENCE_RESULT_TOOLS | DOCUMENT_READ_TOOLS:
            continue
        raw = item.get("output")
        if isinstance(raw, list):
            # Native multimodal output blocks are opaque to this identity view.
            continue
        payload = _source_mapping(raw)
        if payload is None:
            if name in DOCUMENT_READ_TOOLS or name == "record_evidence":
                raise DocumentReadError(name) from TypeError("Source tool output is not a structured mapping")
            # Workspace operations retain their existing non-PDF error contract.
            # They have no structured source fields to project in SDK error prose.
            continue
        # Exact output identity scopes the cached projection. Retained outputs
        # describe historical reads; current writes always reverify the binding.
        key = (name, hashlib.sha256(exact_json(payload).encode("utf-8")).hexdigest())
        if key not in cache:
            cache[key] = exact_json(_compact_reference_view(name, _refresh_document_guidance(
                await project_source_references(payload, user_id=user_id), name)))
        item["output"] = cache[key]
    return result
