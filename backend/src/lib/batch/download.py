"""Disk-backed batch archives with worker-owned SQL and explicit response ownership.

Construction uses AnyIO's shared default thread limiter (40 tokens per process
unless configured by the application). Admission precedes session/tempfile creation.
Cancelled callers leave the shielded worker task alive until completion; its callback
observes errors and closes abandoned results. The limiter stays held throughout work.
"""
import asyncio
import io
import logging
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from .result_files import canonical_result_files
from .service import BatchService
from ...models.sql.database import SessionLocal
from ...models.sql.batch import BatchDocumentStatus
from ...models.sql.file_output import FileOutput
from ..file_outputs.storage import FileOutputStorageService
from ..observability.runtime import report_runtime_exception, sanitized_runtime_error
from ..openai_agents.config import get_batch_zip_metadata_chunk_size

logger = logging.getLogger(__name__)
# Keep strong references until abandoned tasks finish (asyncio only holds weak ones).
_pending_builds: set[asyncio.Task] = set()


def _unique_zip_member_name(filename: str, used_names: set[str]) -> str:
    """Preserve the download filename, suffixing only genuine ZIP collisions."""

    without_control_characters = "".join(
        character
        for character in filename
        if unicodedata.category(character) != "Cc"
    )
    original = Path(without_control_characters.replace("\\", "/")).name
    if original in {"", ".", ".."}:
        original = "result"

    candidate = original
    duplicate_number = 2
    while candidate.casefold() in used_names:
        path = Path(original)
        candidate = f"{path.stem}_{duplicate_number}{path.suffix}"
        duplicate_number += 1

    used_names.add(candidate.casefold())
    return candidate


def _read_metadata(batch_id: UUID, user_id: int, curator_id: str) -> list[tuple[str, str]]:
    """Return plain path/name pairs in manifest order, retaining repeated IDs."""
    with SessionLocal() as db:
        batch = BatchService(db).get_batch(batch_id, user_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found")
        entries = [
            entry for doc in batch.documents
            if doc.status == BatchDocumentStatus.COMPLETED
            for entry in canonical_result_files(doc)
        ]
        if not entries:
            raise HTTPException(status_code=400, detail="No completed documents with results to download")
        ids = []
        for entry in entries:
            try:
                raw_id = entry["file_id"]
                if not isinstance(raw_id, str):
                    raise ValueError("File ID must be a string")
                ids.append(UUID(raw_id))
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping malformed batch result file ID")
        unique_ids = list(dict.fromkeys(ids))
        size = get_batch_zip_metadata_chunk_size()
        metadata = {}
        for offset in range(0, len(unique_ids), size):
            rows = db.query(FileOutput.id, FileOutput.file_path, FileOutput.filename).filter(
                FileOutput.id.in_(unique_ids[offset:offset + size]),
                FileOutput.curator_id == curator_id,
            ).all()
            metadata.update((row.id, (row.file_path, row.filename)) for row in rows)
        return [metadata[file_id] for file_id in ids if file_id in metadata]


def _report_failure(operation: str) -> None:
    report_runtime_exception(
        sanitized_runtime_error("Batch ZIP operation failed"),
        component="batch_zip", operation=operation,
    )


def build_batch_zip(batch_id: UUID, user_id: int, curator_id: str) -> BinaryIO:
    """Own the session and archive until a complete, rewound archive is returned."""
    metadata = _read_metadata(batch_id, user_id, curator_id)
    # SQL transaction has ended before directory creation, path I/O or compression.
    base_path = FileOutputStorageService().base_path.resolve()
    archive = tempfile.TemporaryFile(mode="w+b")
    try:
        files_added = 0
        used_names: set[str] = set()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as writer:
            for path, filename in metadata:
                try:
                    resolved = Path(path).resolve()
                    if not resolved.is_relative_to(base_path) or not resolved.is_file():
                        continue
                    member = _unique_zip_member_name(filename, used_names)
                except (OSError, TypeError, ValueError, RuntimeError):
                    logger.warning("Skipping invalid batch result path")
                    continue
                # ZipFile.write may register a partial member before raising. Roll
                # it back so a recoverable entry failure never exports partial bytes.
                offset = writer.start_dir
                count = len(writer.filelist)
                try:
                    writer.write(resolved, arcname=member)
                except Exception:
                    for info in writer.filelist[count:]:
                        del writer.NameToInfo[info.filename]
                    del writer.filelist[count:]
                    writer.start_dir = offset
                    archive.seek(offset)
                    archive.truncate()
                    _report_failure("entry_write_failed")
                    continue
                files_added += 1
        if not files_added:
            raise HTTPException(status_code=400, detail="No downloadable result files available for this batch")
        archive.seek(0)
        return archive
    except BaseException:
        archive.close()
        raise


class BatchZipResponse(StreamingResponse):
    """Own the archive even if ASGI disconnects before the iterator starts."""

    def __init__(self, archive: BinaryIO, batch_id: UUID):
        self.archive = archive
        super().__init__(
            self._chunks(), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="batch_{batch_id}_results.zip"'},
        )

    def _chunks(self):
        try:
            yield from iter(lambda: self.archive.read(io.DEFAULT_BUFFER_SIZE), b"")
        finally:
            self.archive.close()

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.archive.close()


def _close_abandoned(task: asyncio.Task) -> None:
    _pending_builds.discard(task)
    if task.cancelled():
        return
    try:
        archive = task.result()
    except HTTPException:
        pass  # Expected missing/ineligible resources are not server failures.
    except Exception:
        _report_failure("abandoned_build_failed")
    else:
        archive.close()


async def prepare_batch_zip(batch_id: UUID, user_id: int, curator_id: str) -> BatchZipResponse:
    task = asyncio.create_task(run_sync(build_batch_zip, batch_id, user_id, curator_id))
    _pending_builds.add(task)
    try:
        archive = await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_close_abandoned)
        raise
    except BaseException:
        _pending_builds.discard(task)
        raise
    else:
        _pending_builds.discard(task)
        try:
            return BatchZipResponse(archive, batch_id)
        except BaseException:
            archive.close()
            raise
