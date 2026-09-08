"""No-provider transport tests for the replacement benchmark CLI."""

import json

import httpx
import pytest

from src.lib.benchmark_cli.client import BenchmarkClient, ClientError, Credentials, ExitCode


def credentials():
    return Credentials("test-access", "test-human", "test-source")


@pytest.mark.parametrize("human,source", [(False, False), (True, False), (True, True)])
def test_credentials_are_explicitly_routed(human, source):
    def handle(request):
        assert request.headers["Authorization"] == "Bearer test-access"
        assert ("X-Benchmark-Curator-Authorization" in request.headers) is human
        assert ("X-Benchmark-Delegated-Source-Authorization" in request.headers) is source
        return httpx.Response(200, json={"items": []})
    with BenchmarkClient("https://benchmark.invalid", credentials(), transport=httpx.MockTransport(handle)) as client:
        assert client.request("GET", "/api/v1/benchmarks/jobs", human=human, source=source) == {"items": []}
    assert "test-access" not in repr(credentials())


@pytest.mark.parametrize("status,code", [(401, 3), (403, 3), (409, 4), (422, 2), (500, 8), (302, 8)])
def test_errors_and_redirects_never_echo_body_or_forward_credentials(status, code):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="private paper test-access", headers={"Location": "https://elsewhere.invalid"})
    with BenchmarkClient("https://benchmark.invalid", credentials(), transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ClientError) as caught:
            client.request("POST", "/api/v1/benchmarks/jobs", body={"private": "paper"})
    assert int(caught.value.code) == code
    assert "private" not in str(caught.value)
    assert "test-access" not in str(caught.value)
    assert len(calls) == 1


def test_lost_submit_response_does_not_retry_and_deliberate_recovery_is_identical():
    requests = []
    def handle(request):
        requests.append((request.content, request.headers["Idempotency-Key"]))
        if len(requests) == 1:
            raise httpx.ReadError("private test-access", request=request)
        return httpx.Response(202, json={"job_id": "job"})
    with BenchmarkClient("https://benchmark.invalid", credentials(), transport=httpx.MockTransport(handle)) as client:
        body = {"suite": {}, "plan": {}}
        with pytest.raises(ClientError) as caught:
            client.request("POST", "/api/v1/benchmarks/jobs", body=body, idempotency_key="stable-key", human=True)
        assert caught.value.code == ExitCode.TRANSPORT
        assert len(requests) == 1
        assert client.request("POST", "/api/v1/benchmarks/jobs", body=body, idempotency_key="stable-key", human=True) == {"job_id": "job"}
    assert requests[0] == requests[1]
    assert json.loads(requests[0][0]) == body


# Username-only userinfo exercises the credentialed-origin rejection without
# embedding a password-shaped fixture in source control.
@pytest.mark.parametrize("url", ["http://remote.invalid", "https://test-user@host.invalid", "https://host.invalid/?token=x", "https://host.invalid/#token", "https://host.invalid/api"])
def test_rejects_unsafe_or_credentialed_origins(url):
    with pytest.raises(ClientError):
        BenchmarkClient(url, credentials())


def test_configured_response_bound(monkeypatch):
    monkeypatch.setenv("BENCHMARK_CLI_MAX_RESPONSE_BYTES", "4")
    with BenchmarkClient("http://127.0.0.1:8000", credentials(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"large": "payload"}))) as client:
        with pytest.raises(ClientError, match="byte limit"):
            client.request("GET", "/api/v1/benchmarks/jobs")


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_timeout_must_be_finite_positive(monkeypatch, value):
    monkeypatch.setenv("BENCHMARK_CLI_REQUEST_TIMEOUT_SECONDS", value)
    with pytest.raises(ClientError, match="finite and positive"):
        BenchmarkClient("https://benchmark.invalid", credentials())


def test_missing_human_fails_before_network():
    with BenchmarkClient("https://benchmark.invalid", Credentials("test-access"), transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as client:
        with pytest.raises(ClientError) as caught:
            client.request("GET", "/api/v1/benchmarks/catalog", human=True)
    assert caught.value.code == ExitCode.AUTHORIZATION


def test_timeout_override(monkeypatch):
    monkeypatch.setenv("BENCHMARK_CLI_REQUEST_TIMEOUT_SECONDS", "12.5")
    with BenchmarkClient("https://benchmark.invalid", credentials()) as client:
        assert client.http.timeout.read == 12.5


@pytest.mark.parametrize("suite_id", ["example.v2", "team:case"])
def test_valid_suite_ids_reach_the_exact_endpoint(suite_id):
    def handle(request):
        assert request.url.path == f"/api/v1/benchmarks/suites/{suite_id}"
        return httpx.Response(200, json={"suite": {"suite_id": suite_id}})
    with BenchmarkClient("https://benchmark.invalid", credentials(), transport=httpx.MockTransport(handle)) as client:
        assert client.request("GET", f"/api/v1/benchmarks/suites/{suite_id}", human=True)["suite"]["suite_id"] == suite_id


@pytest.mark.parametrize("path", ["/api/v1/benchmarks/../admin", "/api/v1/benchmarks/%2e%2e/admin", "https://other.invalid", "/api/v1/benchmarks/jobs?token=x"])
def test_paths_cannot_escape_the_api(path):
    with BenchmarkClient("https://benchmark.invalid", credentials(), transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as client:
        with pytest.raises(ClientError, match="Invalid benchmark API path"):
            client.request("GET", path)
