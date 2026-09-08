"""Real PostgreSQL scoping for benchmark flow-result resolution."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest

from src.lib.benchmarks import flow_results, runtime
from src.lib.benchmarks.models import ResolvedBenchmarkCell
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.curation_workspace.models import CurationExtractionResultRecord
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.schemas.curation_workspace import CurationExtractionSourceKind


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.mark.asyncio
async def test_result_resolution_enforces_all_execution_scope_filters(monkeypatch):
    run_id = str(uuid4())
    owner = f"benchmark-flow-results:{uuid4()}"
    with SessionLocal() as session:
        # All synthetic rows live in one transaction, rolled back on close.
        user = User(auth_sub=owner)
        session.add(user)
        session.flush()
        documents = []
        for _ in range(2):
            document_id = uuid4()
            document = PDFDocument(
                id=document_id,
                filename=f"{document_id}.pdf",
                file_path=f"/synthetic-benchmark-test/{document_id}.pdf",
                file_hash=hashlib.sha256(document_id.bytes).hexdigest(),
                file_size=1,
                page_count=1,
                user_id=user.id,
            )
            session.add(document)
            documents.append(document)
        session.flush()
        records = []
        for changes in (
            {},
            {"user_id": "another-owner"},
            {"document_id": documents[1].id},
            {"flow_run_id": "another-run"},
            {"origin_session_id": "another-session"},
            {"source_kind": CurationExtractionSourceKind.CHAT},
        ):
            result_id = uuid4()
            values = {
                "id": result_id,
                "document_id": documents[0].id,
                "adapter_key": "test-adapter",
                "agent_key": "test-extractor",
                "source_kind": CurationExtractionSourceKind.FLOW,
                "origin_session_id": run_id,
                "flow_run_id": run_id,
                "user_id": owner,
                "payload_json": {
                    "envelope_id": f"envelope:{result_id}",
                    "domain_pack_id": "test-pack",
                    "extracted_objects": [],
                },
            }
            record = CurationExtractionResultRecord(**(values | changes))
            session.add(record)
            records.append(record)
        session.flush()
        monkeypatch.setattr(
            flow_results, "list_extraction_results",
            lambda **kwargs: list_extraction_results(db=session, **kwargs),
        )
        completion = {
            "status": "completed",
            "document_id": str(documents[0].id),
            "flow_run_id": run_id,
            "origin_session_id": run_id,
            "extraction_result_refs": [{"extraction_result_id": str(records[0].id)}],
        }
        scope = {"document_id": str(documents[0].id), "user_id": owner, "run_id": run_id}
        cell = ResolvedBenchmarkCell.model_validate({
            "cell_id": "sha256:" + "1" * 64,
            "case_id": "case-1",
            "configuration_id": "config-1",
            "repetition": 1,
            "target": {"kind": "flow", "id": "synthetic-flow"},
            "input": {
                "resolver": "fixture", "reference": "paper-1", "version": "1",
                "digest": "sha256:" + "2" * 64,
            },
            "routes": {"supervisor": {"provider": "openai", "model": "synthetic-model"}},
        })

        async def completed_flow(**kwargs):
            assert kwargs["flow_run_id"] == run_id
            assert kwargs["document_id"] == scope["document_id"]
            assert kwargs["user_id"] == owner
            yield {"type": "FLOW_FINISHED", "data": completion}

        monkeypatch.setattr(runtime, "_flow_from_recipe", lambda _target, _groups: SimpleNamespace(id=uuid4()))
        monkeypatch.setattr(runtime, "execute_flow", completed_flow)
        result = await runtime.execute_resolved_flow_cell(
            cell, {"document_id": scope["document_id"], "user_id": owner}, run_id
        )
        output = result.output
        assert len(output["envelopes"]) == 1
        assert output["envelopes"][0]["metadata"]["source_extraction_result_id"] == str(records[0].id)
        for foreign_record in records[1:]:
            with pytest.raises(ValueError, match="does not match persisted"):
                flow_results.load_flow_extractions(
                    completion | {"extraction_result_refs": [{"extraction_result_id": str(foreign_record.id)}]},
                    **scope,
                )
