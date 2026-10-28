import json
import logging

from src.lib.openai_agents import extraction_trace_events as events


def test_writer_persists_versioned_redacted_bounded_events(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_PREVIEW_LIMIT", "100")
    monkeypatch.setattr(events, "_mirror_to_langfuse", lambda _event: None)

    events.start_extraction_trace_run(
        trace_id="trace-123",
        session_id="session-123",
        user_id="curator@example.org",
        observation_id="obs-root",
    )
    try:
        first = events.write_extraction_trace_event(
            event_type="evidence.operation",
            input_summary={
                "domain_pack_id": "agr.alliance.gene_expression",
                "api_key": "secret-value",
                "paper_text": "x" * 200,
            },
            output_summary={"candidate_id": "gex-candidate-1"},
            validation={"status": "ok"},
        )
        second = events.write_extraction_trace_event(
            event_type="resolver.operation",
            tool_call_id="call-resolve",
            input_summary={"token": "secret-token"},
        )
        metrics = events.write_extraction_trace_event(
            event_type="runtime.provider_context_preflight",
            input_summary={
                "payload_summary": {
                    "estimated_tokens": 123,
                    "input_tokens": 45,
                    "total_token_count": 168,
                    "output_reserve_tokens": 16000,
                    "access_token": "secret-access-token",
                }
            },
        )
    finally:
        events.clear_extraction_trace_run()

    assert first is not None
    assert second is not None
    assert metrics is not None
    assert first["schema_version"] == events.SCHEMA_VERSION
    assert second["sequence"] == first["sequence"] + 1
    assert first["trace_id"] == "trace-123"
    assert first["observation_id"] == "obs-root"
    assert first["session_id"] == "session-123"
    assert first["user_id_hash"]
    assert first["domain_pack_id"] == "agr.alliance.gene_expression"
    assert first["input_summary"]["preview"]["api_key"] == "<redacted>"
    assert first["input_summary"]["preview"]["paper_text"]["truncated"] is True
    assert first["input_summary"]["size"]["json_chars"] > 200
    assert first["input_summary"]["size"]["estimated_tokens"] > 0
    assert first["payload_size_summary"]["input_json_chars"] == first["input_summary"][
        "size"
    ]["json_chars"]
    assert first["event_size"]["json_chars"] > 0
    assert second["input_summary"]["preview"]["token"] == "<redacted>"
    metric_summary = metrics["input_summary"]["preview"]["payload_summary"]
    assert metric_summary["estimated_tokens"] == 123
    assert metric_summary["input_tokens"] == 45
    assert metric_summary["total_token_count"] == 168
    assert metric_summary["output_reserve_tokens"] == 16000
    assert metric_summary["access_token"] == "<redacted>"

    lines = events.trace_event_path("trace-123").read_text(encoding="utf-8").splitlines()
    persisted = [json.loads(line) for line in lines]
    assert [event["event_id"] for event in persisted] == [
        first["event_id"],
        second["event_id"],
        metrics["event_id"],
    ]


def test_stream_event_maps_specialist_tool_and_validation_events(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setattr(events, "_mirror_to_langfuse", lambda _event: None)
    events.start_extraction_trace_run(trace_id="trace-specialist")
    try:
        tool_event = events.write_stream_event(
            {
                "type": "TOOL_START",
                "timestamp": "2026-05-29T00:00:00Z",
                "details": {
                    "toolCallId": "call-specialist",
                    "toolName": "ask_gene_expression_specialist",
                    "isSpecialistInternal": True,
                    "toolArgs": {"gene": "tmem67"},
                },
            }
        )
        complete_event = events.write_stream_event(
            {
                "type": "TOOL_COMPLETE",
                "timestamp": "2026-05-29T00:00:01Z",
                "details": {
                    "toolCallId": "call-specialist",
                    "toolName": "search_document",
                    "isSpecialistInternal": True,
                    "success": True,
                },
                "internal": {
                    "tool_output": {"summary": "found expression evidence"},
                },
            }
        )
        validation_event = events.write_stream_event(
            {
                "type": "SPECIALIST_ERROR",
                "details": {
                    "error": "Candidate failed validation",
                    "reason": "missing_evidence",
                },
            }
        )
    finally:
        events.clear_extraction_trace_run()

    assert tool_event is not None
    assert complete_event is not None
    assert validation_event is not None
    assert tool_event["event_type"] == "specialist_tool_call.started"
    assert complete_event["event_type"] == "specialist_tool_call.completed"
    assert tool_event["tool_call_id"] == "call-specialist"
    assert complete_event["output_summary"]["preview"] == {"summary": "found expression evidence"}
    assert validation_event["event_type"] == "validation.failure"
    assert validation_event["validation"]["status"] == "failed"


def test_writer_logs_debug_when_event_has_no_trace_context(caplog):
    events.clear_extraction_trace_run()

    with caplog.at_level(logging.DEBUG, logger=events.logger.name):
        event = events.write_extraction_trace_event(event_type="specialist_tool_call.started")

    assert event is None
    assert "Dropping extraction trace event without trace context" in caplog.text


def test_writer_logs_large_payload_sizes(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_SIZE_LOG_THRESHOLD_CHARS", "1000")
    monkeypatch.setattr(events, "_mirror_to_langfuse", lambda _event: None)
    events.start_extraction_trace_run(trace_id="trace-large")
    try:
        with caplog.at_level(logging.WARNING, logger=events.logger.name):
            event = events.write_extraction_trace_event(
                event_type="tool_call.completed",
                output_summary="x" * 2000,
                metadata={"tool_name": "huge_tool"},
            )
    finally:
        events.clear_extraction_trace_run()

    assert event is not None
    assert event["output_summary"]["size"]["string_chars"] == 2000
    assert event["payload_size_summary"]["output_json_chars"] >= 2000
    assert "Large extraction trace payload" in caplog.text


def test_mirror_to_langfuse_uses_metadata_payload_to_preserve_trace_io(monkeypatch):
    captured = {}

    class FakeLangfuse:
        def create_event(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.get_langfuse",
        lambda: FakeLangfuse(),
    )

    event = {
        "trace_id": "trace-abc",
        "observation_id": "span-abc",
        "event_type": "runtime.run_finished",
    }
    events._mirror_to_langfuse(event)

    assert captured["name"] == "extraction_trace_event"
    assert "input" not in captured
    assert "output" not in captured
    assert captured["metadata"]["event_payload"] == event
    assert captured["trace_context"] == {
        "trace_id": "trace-abc",
        "parent_span_id": "span-abc",
    }


def test_write_failure_reports_safely_and_still_mirrors(tmp_path, monkeypatch):
    from src.lib.observability import runtime
    root = tmp_path / "not-a-directory"
    root.write_text("occupied")
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(root))
    captured, mirrored = [], []
    monkeypatch.setattr(runtime, "report_runtime_exception", lambda exc, **kw: captured.append((str(exc), kw)))
    monkeypatch.setattr(events, "_mirror_to_langfuse", mirrored.append)
    events.start_extraction_trace_run(trace_id="a" * 32, session_id="session", user_id="owner")
    try:
        result = events.write_extraction_trace_event(event_type="test.failure", input_summary={"paper_text": "private"})
        assert result is not None and mirrored
        assert len(captured) == 1
        assert captured[0][1]["operation"] == "write_failed"
        assert str(root) not in str(captured) and "private" not in str(captured)
        def broken(*args, **kwargs):
            raise RuntimeError("reporting failed")
        monkeypatch.setattr(runtime, "report_runtime_exception", broken)
        assert events.write_extraction_trace_event(event_type="test.failure") is not None
        assert len(mirrored) == 2
    finally:
        events.clear_extraction_trace_run()


def test_capacity_metrics_survive_secret_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setattr(events, "_mirror_to_langfuse", lambda event: None)
    metrics = {"input_tokens_before": 950000, "input_tokens_after": 250000,
        "token_count_requests": 7, "token_count_duration_ms": 142.5,
        "standalone_compactions": 1, "pending_payloads": 2}
    events.start_extraction_trace_run(trace_id="capacity")
    try:
        event = events.write_extraction_trace_event(event_type="runtime.context_capacity",
            output_summary={**metrics, "access_token": "private"})
    finally:
        events.clear_extraction_trace_run()
    assert event["output_summary"]["preview"] == {**metrics, "access_token": "<redacted>"}


def test_append_waits_for_other_file_owner(tmp_path, monkeypatch):
    import fcntl
    import threading

    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    path = events.trace_event_path("locked")
    started = threading.Event()

    def append():
        started.set()
        events._append_jsonl("locked", {"event_id": "waiting"})

    with path.open("a") as owner:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX)
        worker = threading.Thread(target=append)
        worker.start()
        try:
            assert started.wait(5)
            worker.join(0.2)
            assert worker.is_alive(), "append ignored another writer's file lock"
            assert path.read_bytes() == b""
        finally:
            fcntl.flock(owner.fileno(), fcntl.LOCK_UN)
            worker.join(5)
    assert not worker.is_alive()
    assert [json.loads(line) for line in path.read_text().splitlines()] == [{"event_id": "waiting"}]


def _append_mixed_events(directory, writer):
    import os
    os.environ["EXTRACTION_TRACE_EVENT_DIR"] = directory
    for index in range(30):
        events._append_jsonl("concurrent", {
            "event_id": f"{writer}:{index}",
            "schema_version": events.SCHEMA_VERSION,
            "trace_id": "concurrent",
            "payload": "x" * (40000 if index % 2 else 100),
        })


def test_concurrent_threads_and_processes_preserve_every_record(tmp_path, monkeypatch):
    import multiprocessing
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=_append_mixed_events, args=(str(tmp_path), f"process-{i}")) for i in range(2)]
    for process in processes:
        process.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: _append_mixed_events(str(tmp_path), f"thread-{i}"), range(4)))
        for process in processes:
            process.join(30)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
    raw = events.trace_event_path("concurrent").read_bytes()
    assert raw.endswith(b"\n")
    rows = [json.loads(line) for line in raw.splitlines()]
    expected = {f"{kind}-{i}:{j}" for kind, count in (("thread", 4), ("process", 2)) for i in range(count) for j in range(30)}
    assert len(rows) == len(expected)
    assert {row["event_id"] for row in rows} == expected
    # Use the real standalone reader without importing its conflicting src package.
    from pathlib import Path
    reader_root = next(parent / "trace_review/backend/src" for parent in Path(__file__).resolve().parents
                       if (parent / "trace_review/backend/src/analyzers").is_dir())
    monkeypatch.syspath_prepend(str(reader_root))
    from importlib import import_module
    analyzer = import_module("analyzers.extraction_timeline").ExtractionTimelineAnalyzer
    loaded = analyzer.load_durable_events("concurrent")
    assert len(loaded) == len(expected)
    assert {row["event_id"] for row in loaded} == expected
    for row in rows:
        index = int(row["event_id"].split(":")[1])
        assert row["payload"] == "x" * (40000 if index % 2 else 100)


def test_lock_failure_reports_safely_and_still_mirrors(tmp_path, monkeypatch):
    from src.lib.observability import runtime

    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    captured, mirrored = [], []
    monkeypatch.setattr(runtime, "report_runtime_exception", lambda exc, **kw: captured.append((str(exc), kw)))
    monkeypatch.setattr(events, "_mirror_to_langfuse", mirrored.append)

    def fail_lock(*args):
        raise OSError("private-path-secret")

    monkeypatch.setattr(events.fcntl, "flock", fail_lock)
    events.start_extraction_trace_run(trace_id="lock-failed")
    try:
        result = events.write_extraction_trace_event(event_type="test.failure")
    finally:
        events.clear_extraction_trace_run()
    assert mirrored == [result]
    assert len(captured) == 1
    assert captured[0][1]["operation"] == "write_failed"
    assert "private-path-secret" not in str(captured)
    assert events.trace_event_path("lock-failed").read_bytes() == b""


def test_flush_failure_keeps_lock_until_close_and_releases_it(tmp_path, monkeypatch):
    import fcntl
    from pathlib import Path
    import pytest
    from src.lib.observability import runtime

    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    path = events.trace_event_path("flush-failure")
    original_open = Path.open
    captured, checkpoints = [], []
    monkeypatch.setattr(runtime, "report_runtime_exception", lambda exc, **kw: captured.append(kw))

    def assert_locked():
        with original_open(path, "a") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    class FailingFlush:
        def __enter__(self):
            self.handle = original_open(path, "a", encoding="utf-8")
            return self

        def fileno(self):
            return self.handle.fileno()

        def write(self, line):
            return self.handle.write(line)

        def flush(self):
            assert_locked()
            checkpoints.append("flush")
            raise OSError("sensitive flush detail")

        def __exit__(self, *args):
            try:
                assert_locked()
                checkpoints.append("close")
            finally:
                self.handle.close()

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: FailingFlush())
    events._append_jsonl("flush-failure", {"event_id": "one"})
    assert checkpoints == ["flush", "close"]
    assert len(captured) == 1 and captured[0]["operation"] == "write_failed"
    with original_open(path, "a") as contender:
        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    with original_open(path) as reader:
        assert json.load(reader) == {"event_id": "one"}
