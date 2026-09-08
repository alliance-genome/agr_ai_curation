"""External-only synthetic boundaries for the opt-in replacement canary.

No dependency overrides, JWT decoder replacement, prepared-document shortcut,
or fake database. The fixture never loads production credentials.
"""

import hashlib
import json
import os
import socket
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm
from src.api import (
    auth,
    benchmark_auth,
    benchmark_catalog,
    benchmark_jobs,
    benchmark_sources,
)
from src.lib.benchmarks import curator_authorization, runtime, runtime_catalog
from src.lib.openai_agents.provider_usage import (
    ProviderUsageRecord,
    begin_provider_invocation,
    complete_provider_invocation,
    fail_provider_invocation,
)
from src.lib.weaviate_helpers import get_tenant_name
from src.models.sql.database import SessionLocal
from src.models.sql.user import User
from tests.unit.lib.benchmarks.test_suites import _catalog
from weaviate.classes.tenants import Tenant

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_canary"
CLIENT = "canary-service"
HUMAN_CLIENT = "canary-human"
CONTENT = "Synthetic replacement canary evidence. β\r\nNo biological claim.\r\n".encode()
DIGEST = "sha256:" + hashlib.sha256(CONTENT).hexdigest()


class CanaryIdentity:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**public, "kid": "canary", "alg": "RS256", "use": "sig"}]}
        self.subject = f"canary-{uuid4()}"
        self.current = True
        self.lookups = 0

    def token(self, *, human=False, client=CLIENT, scope=None, **changes):
        now = int(datetime.now(timezone.utc).timestamp())
        claims = {
            "iss": ISSUER, "iat": now, "exp": now + 3600,
            "sub": self.subject if human else client,
            "token_use": "id" if human else "access",
            "aud": HUMAN_CLIENT if human else "canary-api",
            "client_id": client,
            "scope": scope if scope is not None else " ".join(
                f"benchmark:{name}" for name in ("read", "run", "cancel", "delete", "source_read")
            ),
            "cognito:username": self.subject,
            "cognito:groups": ["FBStaff"],
        }
        if not human:
            # Cognito client-credentials tokens need not carry aud or sub.
            # Exercise the explicit M2M verifier profile, not generic OIDC.
            for field in ("aud", "sub", "cognito:username", "cognito:groups"):
                claims.pop(field)
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "canary"})

    def admin_get_user(self, *, UserPoolId, Username):
        assert UserPoolId == "us-east-1_canary" and Username == self.subject
        self.lookups += 1
        return {"Enabled": self.current, "Username": Username,
                "UserAttributes": [{"Name": "sub", "Value": self.subject}]}

    def admin_list_groups_for_user(self, *, UserPoolId, Username):
        assert UserPoolId == "us-east-1_canary" and Username == self.subject
        return {"Groups": [{"GroupName": "FBStaff"}]}

    def close(self):
        pass

    def install(self, monkeypatch, tmp_path, connection):
        settings = {
            "DEV_MODE": "false", "AUTH_PROVIDER": "cognito",
            "COGNITO_REGION": "us-east-1", "COGNITO_USER_POOL_ID": "us-east-1_canary",
            "COGNITO_CLIENT_ID": HUMAN_CLIENT, "COGNITO_CLIENT_SECRET": "",
            "COGNITO_DOMAIN": "https://canary.invalid", "COGNITO_REDIRECT_URI": "http://localhost/callback",
            "BENCHMARK_ENABLED": "true", "BENCHMARK_API_ENABLED": "true",
            "BENCHMARK_WORKER_ENABLED": "true", "BENCHMARK_EXECUTION_ENABLED": "true",
            "BENCHMARK_OIDC_ISSUER_URL": ISSUER, "BENCHMARK_OIDC_AUDIENCE": "canary-api",
            "BENCHMARK_OIDC_ALLOWED_CLIENT_IDS": CLIENT + ",canary-other",
            "BENCHMARK_OIDC_COGNITO_M2M_ENABLED": "true",
            "BENCHMARK_OIDC_COGNITO_M2M_CLIENT_IDS": CLIENT + ",canary-other",
            "BENCHMARK_SNAPSHOT_STORE_BACKEND": "filesystem",
            "BENCHMARK_SNAPSHOT_STORE_PATH": str(tmp_path / "snapshots"),
            "PDF_STORAGE_PATH": str(tmp_path / "documents"),
        }
        for key, value in settings.items():
            monkeypatch.setenv(key, value)
        for capability in ("read", "run", "cancel", "delete", "source_read"):
            monkeypatch.setenv(f"BENCHMARK_OIDC_{capability.upper()}_SCOPES", f"benchmark:{capability}")
        benchmark_auth.reset_benchmark_auth_cache()
        monkeypatch.setattr(auth, "_provider", None)
        monkeypatch.setattr(auth, "_provider_failed", False)
        # Only discovery/key delivery are fake; key selection and signature,
        # issuer, audience, expiry and all API capability checks stay real.
        monkeypatch.setattr("src.auth.providers.oidc.OIDCAuthProvider._discover", lambda provider: {
            "issuer": ISSUER, "jwks_uri": "https://canary.invalid/jwks",
        })
        monkeypatch.setattr(PyJWKClient, "fetch_data", lambda client: self.jwks)
        def cognito(service, **kwargs):
            assert service == "cognito-idp"
            return self
        monkeypatch.setattr(curator_authorization.boto3, "client", cognito)
        with SessionLocal() as db:
            user = User(auth_sub=self.subject, is_active=True)
            db.add(user)
            db.commit()
        with connection.session() as client:
            for name in ("DocumentChunk", "PDFDocument"):
                client.collections.get(name).tenants.create([Tenant(name=get_tenant_name(self.subject))])


class CanaryProvider:
    """Deterministic external execution; runtime adapters/usage recorder are real."""

    def __init__(self):
        self.calls = []
        self.fail_once = False
        self.flow_records = []
        self.on_completed_call: Callable[[], object] | None = None

    def install(self, monkeypatch):
        # Use a reviewed synthetic catalog, not a fake resolved plan. Both API
        # preview and admission still independently run the public planner.
        monkeypatch.setattr(runtime_catalog, "build_curator_route_catalog", lambda *args: _catalog())
        monkeypatch.setattr(benchmark_catalog, "build_curator_route_catalog", lambda *args: _catalog())

        def agent(key, **kwargs):
            assert key == "extractor"
            return SimpleNamespace(model=SimpleNamespace(), selected=kwargs)
        monkeypatch.setattr(runtime, "get_agent_by_id", agent)
        monkeypatch.setattr(runtime, "run_agent_streamed", self.stream)
        monkeypatch.setattr(runtime, "_flow_from_recipe", lambda target, groups: SimpleNamespace(id=target))
        monkeypatch.setattr(runtime, "execute_flow", self.flow)
        async def abstract(raw_text):
            return "Synthetic canary abstract."
        monkeypatch.setattr("src.lib.openai_agents.prompt_utils._extract_abstract_with_llm", abstract)
        async def hierarchy(elements):
            return elements, {}
        async def figures(chunks, **kwargs):
            return chunks
        monkeypatch.setattr("src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy", hierarchy)
        monkeypatch.setattr("src.lib.pipeline.figure_locator_resolution.resolve_figure_locators", figures)

    async def stream(self, **kwargs):
        from src.lib.weaviate_client.chunks import get_chunks_from_index
        selected = kwargs["agent"].selected
        assert kwargs["doc_context"] is not None
        assert selected["db_user_id"] is not None
        chunks = await get_chunks_from_index(kwargs["document_id"], 0, kwargs["user_id"])
        assert chunks and "Synthetic replacement canary evidence." in str(chunks)
        provider, model = selected["model_provider_override"], selected["model_id_override"]
        slot = selected["benchmark_route_slot"]
        self.calls.append((slot, provider, model, kwargs["document_id"]))
        pending = begin_provider_invocation(
            route_slot=slot, requested_provider=provider, requested_model=model,
            reasoning_effort=selected["model_reasoning_override"], started_at=time.perf_counter(),
        )
        if self.fail_once:
            self.fail_once = False
            error = RuntimeError("synthetic external provider failure")
            fail_provider_invocation(pending, error, latency_ms=1)
            raise error
        complete_provider_invocation(pending, ProviderUsageRecord(
            requested_provider=provider, requested_model=model,
            actual_provider=provider, actual_model=model, routing_attempt=0,
            latency_ms=1, input_tokens=2, output_tokens=3, total_tokens=5, billed_cost=None,
        ))
        if self.on_completed_call is not None:
            self.on_completed_call()
        yield {"type": "STRUCTURED_RESULT", "data": {"result": {"records": [{"synthetic": True}], "value": -0.0}}}
        yield {"type": "RUN_FINISHED"}

    async def flow(self, **kwargs):
        from src.lib.curation_workspace.extraction_results import (
            persist_extraction_result,
        )
        from src.schemas.curation_workspace import (
            CurationExtractionPersistenceRequest,
            CurationExtractionSourceKind,
        )
        from tests.unit.lib.curation_workspace.test_curation_prep_service import (
            _make_domain_envelope_extraction_result,
        )

        # The public flow adapter supplies independently frozen named roles.
        # Synthetic calls emit the same observer events as a real provider;
        # result persistence and completion-reference loading are not mocked.
        for slot, route in kwargs["benchmark_routes"].items():
            self.calls.append((slot, route.provider, route.model, kwargs["document_id"]))
            pending = begin_provider_invocation(
                route_slot=slot, requested_provider=route.provider, requested_model=route.model,
                reasoning_effort=route.reasoning_effort, started_at=time.perf_counter(),
            )
            complete_provider_invocation(pending, ProviderUsageRecord(
                requested_provider=route.provider, requested_model=route.model,
                actual_provider=route.provider, actual_model=route.model, routing_attempt=0,
                latency_ms=1, input_tokens=2, output_tokens=3, total_tokens=5, billed_cost=None,
            ))
        fixture = _make_domain_envelope_extraction_result(document_id=kwargs["document_id"])
        payload = fixture.payload_json
        assert isinstance(payload, dict)
        payload["summary"] = "Synthetic transport fixture; no biological correctness claim."
        payload["curatable_objects"][0]["payload"]["verified_quote"] = CONTENT.decode().splitlines()[0]
        record = persist_extraction_result(CurationExtractionPersistenceRequest(
            document_id=kwargs["document_id"], agent_key="gene_extractor", adapter_key="gene",
            source_kind=CurationExtractionSourceKind.FLOW, origin_session_id=kwargs["session_id"], flow_run_id=kwargs["flow_run_id"],
            user_id=kwargs["user_id"], candidate_count=1, payload_json=payload,
            metadata={"project_key": "agr"},
        )).extraction_result
        self.flow_records.append(record)
        yield {"type": "FLOW_FINISHED", "data": {
            "status": "completed", "document_id": kwargs["document_id"],
            "flow_run_id": kwargs["flow_run_id"], "origin_session_id": kwargs["session_id"],
            "extraction_result_refs": [{"extraction_result_id": record.extraction_result_id}],
        }}


@contextmanager
def canary_server():
    from src.api.curation_workspace import router as workspace_router
    from src.lib.benchmarks.input_resolvers import (
        BenchmarkInputResolver,
        BenchmarkInputResolverCatalog,
    )
    from src.lib.benchmarks.snapshots import FrozenBenchmarkSnapshotResolver
    from src.lib.openai_agents.config import (
        get_benchmark_max_input_bytes,
        get_benchmark_source_timeout_seconds,
    )
    app = FastAPI()
    # Real registered frozen resolver; no private or source-service dependency.
    app.state.benchmark_input_resolvers = BenchmarkInputResolverCatalog(
        (cast(BenchmarkInputResolver, FrozenBenchmarkSnapshotResolver()),), timeout_seconds=get_benchmark_source_timeout_seconds(),
        max_input_bytes=get_benchmark_max_input_bytes(),
    )
    for router in (benchmark_catalog.router, benchmark_jobs.router, benchmark_sources.router, workspace_router):
        app.include_router(router)
    assert not app.dependency_overrides
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", access_log=False, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        startup_seconds = float(os.getenv("BENCHMARK_CANARY_SERVER_TIMEOUT_SECONDS", "30"))
        deadline = time.monotonic() + startup_seconds
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Canary API failed to start"
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=startup_seconds)
        sock.close()
        assert not thread.is_alive()
