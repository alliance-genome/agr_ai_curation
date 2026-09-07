"""Persistent-worker reuse, isolation and lifecycle contracts."""

import concurrent.futures
import json
import subprocess
import sys
import threading

import pytest

from src.lib.packages.worker_pool import PackageWorkerPool
from .test_package_runner import _build_runner


@pytest.fixture
def pool():
    value = PackageWorkerPool(capacity=2, response_max_bytes=100_000)
    yield value
    value.close()


@pytest.fixture
def command(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text('''import sys,json,os,time
for line in sys.stdin:
    value=json.loads(line)
    if value.get("crash"): os._exit(2)
    if value.get("bad"): print("invalid",flush=True); continue
    time.sleep(value.get("sleep",0))
    print(json.dumps({"status":"ok","result":{"pid":os.getpid(),"value":value}}),flush=True)
''')
    return [sys.executable, str(script)]


def call(pool, command, payload=None, key=("package",), timeout=3):
    result = pool.execute(key=key, command=command, payload=json.dumps(payload or {}), timeout=timeout)
    return json.loads(result.stdout)["result"]


def test_reuses_matching_process_and_bounds_total_processes(pool, command):
    first = call(pool, command)
    assert call(pool, command)["pid"] == first["pid"]
    assert call(pool, command, key=("new-revision",))["pid"] != first["pid"]
    call(pool, command, key=("third-package",))
    assert len(pool._workers) == 2


@pytest.mark.parametrize("payload,error", [({"crash": True}, RuntimeError), ({"bad": True}, ValueError), ({"sleep": 2}, subprocess.TimeoutExpired)])
def test_failed_worker_is_discarded_without_replaying_request(pool, command, payload, error):
    pid = call(pool, command)["pid"]
    with pytest.raises(error):
        call(pool, command, payload, timeout=.1)
    assert not pool._workers
    assert call(pool, command)["pid"] != pid


def test_concurrent_requests_have_exclusive_workers_and_clean_shutdown(pool, command):
    barrier = threading.Barrier(3)
    def run(value):
        barrier.wait()
        return call(pool, command, {"value": value, "sleep": .1})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(run, 1)
        two = executor.submit(run, 2)
        barrier.wait()
        rows = [one.result(), two.result()]
    assert rows[0]["pid"] != rows[1]["pid"]
    assert [r["value"]["value"] for r in rows] == [1, 2]
    processes = [w.process for w in pool._workers]
    pool.close()
    assert all(p.poll() is not None for p in processes)
    with pytest.raises(RuntimeError, match="closed"):
        call(pool, command)


def test_oversized_response_discards_worker(command):
    pool = PackageWorkerPool(capacity=1, response_max_bytes=10)
    try:
        with pytest.raises(ValueError, match="byte limit"):
            call(pool, command)
        assert not pool._workers
    finally:
        pool.close()


def test_real_entrypoint_does_not_retain_request_context(monkeypatch, tmp_path):
    monkeypatch.setenv("PACKAGE_RUNNER_REUSE_WORKERS", "true")
    monkeypatch.setenv("PACKAGE_RUNNER_WORKER_COUNT", "1")
    runner = _build_runner(monkeypatch, tmp_path)
    try:
        first = runner.execute_tool("sdk_static_context_probe", context={"user_id": "alice", "session_id": "session-a", "trace_id": "trace-a"})
        assert first.ok
        assert first.result["user_id"] == "alice"
        pid = runner._worker_pool._workers[0].process.pid
        second = runner.execute_tool("sdk_static_context_probe", context={"user_id": "bob"})
        assert second.ok
        assert second.result["user_id"] == "bob"
        assert second.result["session_id"] is None
        assert second.result["trace_id"] is None
        empty = runner.execute_tool("sdk_static_context_probe")
        assert empty.ok
        assert empty.result["user_id"] is None
        assert runner._worker_pool._workers[0].process.pid == pid
        missing = runner.execute_tool("build_message", kwargs={"subject": "test"})
        assert not missing.ok
        assert "requires execution context" in missing.error.message
    finally:
        runner.close()
