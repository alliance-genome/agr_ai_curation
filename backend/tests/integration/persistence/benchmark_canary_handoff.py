"""Real SQL prep/snapshot delivery to a deterministic HTTP boundary, not a portal."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
from src.lib.curation_workspace import benchmark_snapshots
from src.lib.curation_workspace.curation_prep_service import run_curation_prep
from src.lib.curation_workspace.models import CurationReviewSession, DomainEnvelopeModel
from src.models.sql.database import SessionLocal
from src.schemas.curation_prep import CurationPrepScopeConfirmation
from tests.integration.persistence.benchmark_canary_support import ISSUER


def check_handoff(monkeypatch, client, origin, identity, record):
    # Deterministic prep materializes the real persisted flow result. Review
    # session association is seeded; this is not a claim of browser bootstrap
    # or model validation, which have their own tests.
    prep = asyncio.run(run_curation_prep([record], scope_confirmation=CurationPrepScopeConfirmation(
        confirmed=True, adapter_keys=["gene"], notes=["Synthetic canary scope only."],
    )))
    assert prep.review_row_count == 1
    ref = prep.envelope_refs[0]
    with SessionLocal() as db:
        session = CurationReviewSession(
            document_id=UUID(record.document_id), adapter_key="gene", created_by_id=identity.subject,
            prepared_at=datetime.now(timezone.utc), flow_run_id=record.flow_run_id,
        )
        db.add(session)
        db.flush()
        envelope = db.get(DomainEnvelopeModel, ref.envelope_id)
        assert envelope is not None
        envelope.session_id = session.id
        session_id = str(session.id)
        db.commit()
    base = origin + "/api/curation-workspace"
    human_headers = {"Cookie": "auth_token=" + identity.token(human=True)}
    exported = client.http.post(base + f"/sessions/{session_id}/envelopes/{ref.envelope_id}/benchmark-snapshots",
        headers=human_headers, json={"expected_revision": ref.envelope_revision})
    assert exported.status_code == 200, exported.text
    snapshot = exported.json()
    snapshot_id = snapshot["snapshot_id"]
    download = client.http.get(base + f"/benchmark-snapshots/{snapshot_id}/download", headers=human_headers)
    assert download.status_code == 200
    body = download.content
    calls = []
    deliveries = []
    destinations = {}
    for name in ("success", "uncertain"):
        destinations[name] = {
            "label": "Synthetic " + name, "sink_url": f"https://sink.invalid/{name}",
            "token_url": "https://sink.invalid/token", "client_id": "synthetic-handoff",
            "scope": "synthetic/send", "client_secret_env": "CANARY_HANDOFF_SECRET",
            "allowed_redirect_origin": "https://sink.invalid", "allowed_redirect_path_prefix": "/comparisons",
        }
    monkeypatch.setenv("BENCHMARK_SNAPSHOT_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_SNAPSHOT_HANDOFF_DESTINATIONS_JSON", json.dumps(destinations))
    monkeypatch.setenv("CANARY_HANDOFF_SECRET", "synthetic-only-no-credential")

    def handle(request):
        assert request.url.host == "sink.invalid"
        calls.append(request.url.path)
        if request.url.path == "/token":
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "synthetic-sink-token"})
        assert request.content == body
        assert request.headers["X-Curation-Benchmark-Sender-Issuer"] == ISSUER
        assert request.headers["X-Curation-Benchmark-Sender-Subject"] == identity.subject
        assert request.headers["Authorization"] == "Bearer synthetic-sink-token"
        deliveries.append((request.url.path, request.headers["Idempotency-Key"], hashlib.sha256(request.content).hexdigest()))
        if request.url.path == "/uncertain":
            raise httpx.ReadTimeout("Synthetic sink accepted bytes, then connection ended", request=request)
        return httpx.Response(200, json={"receipt_id": "synthetic-receipt", "redirect_url": "https://sink.invalid/comparisons/synthetic"})

    real_client = httpx.AsyncClient
    # Replace only this module's outbound transport, not inbound auth/CLI HTTP.
    from types import SimpleNamespace
    monkeypatch.setattr(benchmark_snapshots, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
        Timeout=httpx.Timeout, TimeoutException=httpx.TimeoutException, RequestError=httpx.RequestError,
    ))
    for destination, expected in (("success", "succeeded"), ("uncertain", "unknown")):
        response = client.http.post(base + f"/benchmark-snapshots/{snapshot_id}/handoffs", headers=human_headers,
            json={"destination_id": destination})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected
        before = len(calls)
        replay = client.http.post(base + f"/benchmark-snapshots/{snapshot_id}/handoffs", headers=human_headers,
            json={"destination_id": destination})
        assert replay.json()["status"] == expected and len(calls) == before
    assert len(deliveries) == 2
    return {"exact_bytes": True, "durable_replay": True, "uncertain_resent": False}
