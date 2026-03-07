"""Runtime service for upload intake choreography.

The intake output contract is `UploadIntakeResult`, which includes:
- `document_id`
- `job_id`
- initial `status` (always `PENDING`)
- ownership and tenant metadata used by the upload response
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import BackgroundTasks, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import get_pdf_storage_path
from src.lib.pdf_jobs.upload_execution_service import UploadExecutionRequest, UploadExecutionService
from src.lib.pipeline.upload import PDFUploadHandler
from src.lib.weaviate_client.documents import create_document, delete_document, get_document
from src.lib.weaviate_helpers import get_tenant_name
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument as ViewerPDFDocument
from src.services.user_service import principal_from_claims, provision_user
from . import service as pdf_job_service

logger = logging.getLogger(__name__)


class UploadIntakeValidationError(ValueError):
    """Raised when an upload request fails validation checks."""


class UploadIntakeDuplicateError(Exception):
    """Raised when upload intake detects a duplicate document."""

    def __init__(self, detail: Dict[str, Any]):
        super().__init__("duplicate upload")
        self.detail = detail


@dataclass(frozen=True)
class UploadIntakeResult:
    """Intake output contract consumed by the HTTP endpoint layer."""

    document_id: str
    job_id: str
    user_id: int
    filename: str
    status: str
    upload_timestamp: datetime
    processing_started_at: Optional[datetime]
    processing_completed_at: Optional[datetime]
    file_size_bytes: int
    weaviate_tenant: str
    chunk_count: Optional[int]
    error_message: Optional[str]


class UploadIntakeService:
    """Orchestrate upload intake from file receipt through durable job dispatch."""

    def __init__(
        self,
        *,
        upload_execution_service: UploadExecutionService,
        session_factory: Callable[[], Session] = SessionLocal,
        storage_path_provider: Callable[[], Path] = get_pdf_storage_path,
        upload_handler_factory: Optional[Callable[[Path], PDFUploadHandler]] = None,
        principal_from_claims_fn: Callable[[Dict[str, Any]], Any] = principal_from_claims,
        provision_user_fn: Callable[[Session, Any], Any] = provision_user,
        create_document_fn: Callable[[str, Any], Any] = create_document,
        get_document_fn: Callable[[str, str], Any] = get_document,
        delete_document_fn: Callable[[str, str], Any] = delete_document,
        create_job_fn: Callable[..., Any] = pdf_job_service.create_job,
        tenant_name_resolver: Callable[[str], str] = get_tenant_name,
    ) -> None:
        self.upload_execution_service = upload_execution_service
        self._session_factory = session_factory
        self._storage_path_provider = storage_path_provider
        self._upload_handler_factory = upload_handler_factory or self._default_upload_handler_factory
        self._principal_from_claims = principal_from_claims_fn
        self._provision_user = provision_user_fn
        self._create_document = create_document_fn
        self._get_document = get_document_fn
        self._delete_document = delete_document_fn
        self._create_job = create_job_fn
        self._tenant_name_resolver = tenant_name_resolver

    @staticmethod
    def _default_upload_handler_factory(storage_path: Path) -> PDFUploadHandler:
        return PDFUploadHandler(storage_path=storage_path)

    async def intake_upload(
        self,
        *,
        background_tasks: BackgroundTasks,
        file: UploadFile,
        user: Dict[str, Any],
    ) -> UploadIntakeResult:
        """Execute upload intake choreography and return response-ready output."""
        self._validate_pdf_filename(file.filename)

        user_sub = user["sub"]
        base_storage = self._storage_path_provider()
        user_storage_path = base_storage / user_sub
        user_storage_path.mkdir(parents=True, exist_ok=True)

        upload_handler = self._upload_handler_factory(user_storage_path)
        saved_path, document = await upload_handler.save_uploaded_pdf(file)

        session = self._session_factory()
        db_user = None
        weaviate_document_created = False

        try:
            db_user = self._provision_user(session, self._principal_from_claims(user))

            raw_checksum = document.metadata.checksum
            scoped_file_hash = hashlib.sha256(f"{db_user.id}:{raw_checksum}".encode("utf-8")).hexdigest()

            existing = (
                session.execute(
                    select(ViewerPDFDocument).where(
                        ViewerPDFDocument.user_id == db_user.id,
                        or_(
                            ViewerPDFDocument.file_hash == scoped_file_hash,
                            ViewerPDFDocument.file_hash == raw_checksum,
                        ),
                    )
                )
                .scalars()
                .first()
            )

            if existing:
                existing = await self._resolve_phantom_duplicate(
                    session=session,
                    existing=existing,
                    user_sub=user_sub,
                )

            if existing:
                self._cleanup_saved_file_artifacts(saved_path)
                raise UploadIntakeDuplicateError(
                    {
                        "error": "duplicate_file",
                        "message": (
                            "This file has already been uploaded on "
                            f"{existing.upload_timestamp.strftime('%B %d, %Y at %I:%M %p')}"
                        ),
                        "existing_document_id": str(existing.id),
                        "uploaded_at": existing.upload_timestamp.isoformat(),
                        "suggestion": (
                            "If you want to re-process this file, delete the existing document "
                            "first and then upload again."
                        ),
                    }
                )

            await self._create_document(user_sub, document)
            weaviate_document_created = True

            relative_path = saved_path.relative_to(base_storage)
            record = ViewerPDFDocument(
                id=uuid.UUID(str(document.id)),
                filename=document.filename,
                file_path=str(relative_path).replace("\\", "/"),
                file_hash=scoped_file_hash,
                file_size=saved_path.stat().st_size,
                page_count=max(document.metadata.page_count, 1),
                user_id=db_user.id,
            )
            session.add(record)
            session.commit()

        except UploadIntakeDuplicateError:
            raise
        except IntegrityError as integrity_error:
            session.rollback()
            logger.warning(
                "Integrity error persisting PDF metadata for document %s (user_id=%s): %s",
                document.id,
                getattr(db_user, "id", "unknown"),
                integrity_error,
            )
            await self._compensate_persistence_failure(
                user_sub=user_sub,
                document_id=str(document.id),
                saved_path=saved_path,
                weaviate_document_created=weaviate_document_created,
            )
            raise UploadIntakeDuplicateError(
                {
                    "error": "duplicate_file",
                    "message": "This file appears to have already been uploaded for your account.",
                    "suggestion": "Refresh the document list. If needed, delete the existing document and upload again.",
                }
            ) from integrity_error
        except Exception:
            session.rollback()
            await self._compensate_persistence_failure(
                user_sub=user_sub,
                document_id=str(document.id),
                saved_path=saved_path,
                weaviate_document_created=weaviate_document_created,
            )
            raise
        finally:
            session.close()

        try:
            job = self._create_job(
                document_id=document.id,
                user_id=db_user.id,
                filename=document.filename,
            )
        except Exception:
            await self._compensate_job_creation_failure(
                user_sub=user_sub,
                document_id=str(document.id),
                saved_path=saved_path,
                weaviate_document_created=weaviate_document_created,
            )
            raise
        job_id = job.job_id

        execution_request = UploadExecutionRequest(
            document_id=document.id,
            job_id=job_id,
            user_id=user_sub,
            file_path=saved_path,
        )
        await self.upload_execution_service.dispatch_upload_execution(
            background_tasks=background_tasks,
            request=execution_request,
        )

        return UploadIntakeResult(
            document_id=document.id,
            job_id=job_id,
            user_id=db_user.id,
            filename=document.filename,
            status="PENDING",
            upload_timestamp=datetime.now(timezone.utc),
            processing_started_at=None,
            processing_completed_at=None,
            file_size_bytes=saved_path.stat().st_size,
            weaviate_tenant=self._tenant_name_resolver(user_sub),
            chunk_count=None,
            error_message=None,
        )

    async def _compensate_job_creation_failure(
        self,
        *,
        user_sub: str,
        document_id: str,
        saved_path: Path,
        weaviate_document_created: bool,
    ) -> None:
        cleanup_session = self._session_factory()
        try:
            persisted = (
                cleanup_session.execute(
                    select(ViewerPDFDocument).where(
                        ViewerPDFDocument.id == uuid.UUID(str(document_id)),
                    )
                )
                .scalars()
                .first()
            )
            if persisted:
                cleanup_session.delete(persisted)
                cleanup_session.commit()
        except Exception as cleanup_err:
            cleanup_session.rollback()
            logger.warning(
                "Best-effort SQL cleanup failed after durable job creation error for document %s: %s",
                document_id,
                cleanup_err,
            )
        finally:
            cleanup_session.close()

        await self._compensate_persistence_failure(
            user_sub=user_sub,
            document_id=document_id,
            saved_path=saved_path,
            weaviate_document_created=weaviate_document_created,
        )

    async def _resolve_phantom_duplicate(
        self,
        *,
        session: Session,
        existing: ViewerPDFDocument,
        user_sub: str,
    ) -> Optional[ViewerPDFDocument]:
        try:
            existing_weaviate_doc = await self._get_document(user_sub, str(existing.id))
            if existing_weaviate_doc:
                return existing

            logger.warning(
                "Phantom document detected (hash match in PG, missing in Weaviate): %s. Cleaning up old record.",
                existing.id,
            )
            session.delete(existing)
            session.commit()
            return None
        except ValueError as not_found_err:
            logger.warning(
                "Phantom document detected (ValueError - not in Weaviate): %s. Cleaning up: %s",
                existing.id,
                not_found_err,
            )
            session.delete(existing)
            session.commit()
            return None
        except Exception as check_err:
            logger.error("Error checking phantom status: %s", check_err)
            return existing

    async def _compensate_persistence_failure(
        self,
        *,
        user_sub: str,
        document_id: str,
        saved_path: Path,
        weaviate_document_created: bool,
    ) -> None:
        """Best-effort compensation for partial persistence failures."""
        if weaviate_document_created:
            try:
                await self._delete_document(user_sub, document_id)
            except Exception as cleanup_err:
                logger.warning(
                    "Best-effort Weaviate cleanup failed for document %s: %s",
                    document_id,
                    cleanup_err,
                )

        self._cleanup_saved_file_artifacts(saved_path)

    @staticmethod
    def _cleanup_saved_file_artifacts(saved_path: Path) -> None:
        if saved_path.exists():
            shutil.rmtree(saved_path.parent, ignore_errors=True)

    @staticmethod
    def _validate_pdf_filename(filename: Optional[str]) -> None:
        if not filename or not filename.lower().endswith(".pdf"):
            raise UploadIntakeValidationError(f"File must be a PDF. Got: {filename}")
