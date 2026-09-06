import httpx
import pytest

from src.lib.benchmark_cli.client import BenchmarkClient, ClientError, Credentials, ExitCode
from src.lib.benchmark_cli import watch as observation

JOB = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(observation.time, "sleep", lambda _: None)
    monkeypatch.setenv("BENCHMARK_CLI_EVENT_RECONNECT_ATTEMPTS", "1")


class FakeClient:
    def __init__(self, streams, statuses):
        self.streams = iter(streams)
        self.statuses = iter(statuses)
        self.cursors = []
        self.requests = []

    def events(self, job_id, cursor):
        assert job_id == JOB
        self.cursors.append(cursor)
        events = next(self.streams)
        if isinstance(events, Exception):
            raise events
        yield from events

    def request(self, method, path):
        self.requests.append((method, path))
        assert method == "GET"
        assert path.endswith(JOB)
        status = next(self.statuses)
        if isinstance(status, Exception):
            raise status
        return {"summary": {"id": JOB, "status": status}}


@pytest.mark.parametrize("status,code", [("completed", 0), ("completed_with_failures", 5), ("failed", 5), ("cancelled", 6)])
def test_terminal_status_exit_codes(status, code):
    client = FakeClient([[('job.status', None, {"id": JOB, "status": status})]], [])
    assert observation.watch(client, JOB, lambda _: None) == code
    assert client.requests == []


@pytest.mark.parametrize("cursor", ["malformed", "00000000-0000-4000-8000-000000000002:1"])
def test_invalid_input_cursor_fails_before_network(cursor):
    client = FakeClient([], [])
    with pytest.raises(ClientError) as error:
        observation.watch(client, JOB, lambda _: None, cursor=cursor)
    assert error.value.code == ExitCode.VALIDATION
    assert client.requests == []
    assert client.cursors == []


def test_durable_event_cursor_survives_status_without_id():
    frames = [("benchmark.event", JOB + ":2", {}), ("job.status", None, {"id": JOB, "status": "running"})]
    client = FakeClient([frames, [("job.status", None, {"id": JOB, "status": "completed"})]], ["running"])
    emitted = []
    assert observation.watch(client, JOB, emitted.append) == 0
    assert client.cursors == [None, JOB + ":2"]
    assert emitted[-1]["last_event_id"] == JOB + ":2"


@pytest.mark.parametrize("http", [False, True])
def test_history_expiry_refreshes_status_before_resuming(http):
    detail = {"code": "event_history_expired", "resume_after": JOB + ":8"}
    first = ClientError(ExitCode.CONFLICT, "expired", resume_after=detail["resume_after"]) if http else [("stream.error", None, detail)]
    client = FakeClient([first, [("job.status", None, {"id": JOB, "status": "completed"})]], ["running"])
    assert observation.watch(client, JOB, lambda _: None, cursor=JOB + ":1") == 0
    assert client.requests == [("GET", f"/api/v1/benchmarks/jobs/{JOB}")]
    assert client.cursors == [JOB + ":1", JOB + ":8"]


def test_eof_without_terminal_status_is_interruption():
    client = FakeClient([[], []], ["running", "running"])
    with pytest.raises(ClientError) as caught:
        observation.watch(client, JOB, lambda _: None)
    assert caught.value.code == ExitCode.TRANSPORT
    assert len(client.requests) == 2


def test_authorization_loss_does_not_reconnect_or_poll():
    client = FakeClient([ClientError(ExitCode.AUTHORIZATION, "denied")], [])
    with pytest.raises(ClientError) as caught:
        observation.watch(client, JOB, lambda _: None, polling=True)
    assert caught.value.code == ExitCode.AUTHORIZATION
    assert len(client.cursors) == 1
    assert not client.requests


def test_failed_reconciliation_uses_remaining_observation_budget():
    client = FakeClient([[], [("job.status", None, {"id": JOB, "status": "completed"})]], [ClientError(ExitCode.TRANSPORT, "offline")])
    assert observation.watch(client, JOB, lambda _: None, cursor=JOB + ":2") == 0
    assert client.cursors == [JOB + ":2", JOB + ":2"]


def test_history_cursor_does_not_advance_before_successful_reconciliation():
    gap = ClientError(ExitCode.CONFLICT, "expired", resume_after=JOB + ":8")
    client = FakeClient([gap, []], [ClientError(ExitCode.TRANSPORT, "offline"), "completed"])
    assert observation.watch(client, JOB, lambda _: None, cursor=JOB + ":2") == 0
    assert client.cursors == [JOB + ":2", JOB + ":2"]


def test_failed_poll_recovers_within_deadline(monkeypatch):
    monkeypatch.setenv("BENCHMARK_CLI_EVENT_RECONNECT_ATTEMPTS", "0")
    client = FakeClient([[]], ["running", ClientError(ExitCode.TRANSPORT, "offline"), "completed"])
    assert observation.watch(client, JOB, lambda _: None, polling=True) == 0


@pytest.mark.parametrize("status,code", [(409, 4), (422, 2), (401, 3), (403, 3)])
def test_sse_http_error_codes(status, code):
    with BenchmarkClient("https://benchmark.invalid", Credentials("test-access"), transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private"))) as client:
        with pytest.raises(ClientError) as caught:
            list(client.events(JOB))
    assert caught.value.code == code
    assert "private" not in str(caught.value)


def test_polling_fallback_is_explicit_and_can_observe_completion():
    client = FakeClient([[], []], ["running", "running", "completed"])
    assert observation.watch(client, JOB, lambda _: None, polling=True) == 0
    assert len(client.requests) == 3


def test_polling_fallback_has_finite_deadline(monkeypatch):
    monkeypatch.setenv("BENCHMARK_CLI_EVENT_RECONNECT_ATTEMPTS", "0")
    monkeypatch.setenv("BENCHMARK_CLI_POLL_TIMEOUT_SECONDS", "1")
    ticks = iter([0, 2])
    monkeypatch.setattr(observation.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ClientError, match="timed out"):
        observation.watch(FakeClient([[]], ["running"]), JOB, lambda _: None, polling=True)


@pytest.mark.parametrize("setting", ["BENCHMARK_CLI_POLL_TIMEOUT_SECONDS", "BENCHMARK_CLI_POLL_INTERVAL_SECONDS"])
def test_infinite_timings_rejected(monkeypatch, setting):
    monkeypatch.setenv(setting, "inf")
    with pytest.raises(ClientError, match="finite"):
        observation.watch(FakeClient([], []), JOB, lambda _: None)


def test_event_transport_routes_only_orchestration_and_decodes_frames():
    def handle(request):
        assert request.headers["Last-Event-ID"] == JOB + ":1"
        assert "x-benchmark-curator-authorization" not in request.headers
        assert "x-benchmark-delegated-source-authorization" not in request.headers
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=f'id: {JOB}:2\nevent: benchmark.event\ndata: {{"sequence":2}}\n\nevent: job.status\ndata: {{"status":"running"}}\n\n')
    with BenchmarkClient("https://benchmark.invalid", Credentials("test-access", "test-human", "test-source"), transport=httpx.MockTransport(handle)) as client:
        frames = list(client.events(JOB, JOB + ":1"))
    assert frames == [("benchmark.event", JOB + ":2", {"sequence": 2}), ("job.status", None, {"status": "running"})]


def test_http410_exposes_only_recovery_cursor():
    response = {"detail": {"code": "event_history_expired", "resume_after": JOB + ":8", "message": "private paper"}}
    with BenchmarkClient("https://benchmark.invalid", Credentials("test-access"), transport=httpx.MockTransport(lambda _: httpx.Response(410, json=response))) as client:
        with pytest.raises(ClientError) as caught:
            list(client.events(JOB))
    assert caught.value.resume_after == JOB + ":8"
    assert "private" not in str(caught.value)


def test_oversize_frame_fails(monkeypatch):
    monkeypatch.setenv("BENCHMARK_CLI_MAX_RESPONSE_BYTES", "5")
    with BenchmarkClient("https://benchmark.invalid", Credentials("test-access"), transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content='data: {"large":true}\n\n'))) as client:
        with pytest.raises(ClientError, match="byte limit"):
            list(client.events(JOB))


def test_first_live_frame_is_yielded_before_next_network_chunk_or_eof():
    progress = []
    class LiveStream(httpx.SyncByteStream):
        def __iter__(self):
            progress.append("first")
            yield b'event: job.status\ndata: {"status":"running"}\n\n'
            progress.append("second")
            yield b'event: job.status\ndata: {"status":"completed"}\n\n'
            progress.append("eof")
    def handle(_):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=LiveStream())
    with BenchmarkClient("https://benchmark.invalid", Credentials("test-access"), transport=httpx.MockTransport(handle)) as client:
        events = client.events(JOB)
        assert next(events)[2]["status"] == "running"
        assert progress == ["first"]
        assert next(events)[2]["status"] == "completed"
        assert progress == ["first", "second"]
        events.close()
