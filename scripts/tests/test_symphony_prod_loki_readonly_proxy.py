#!/usr/bin/env python3
"""Focused tests for the Symphony read-only Loki proxy."""

from __future__ import annotations

import http.server
import json
import pathlib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROXY = REPO_ROOT / "scripts" / "utilities" / "symphony_prod_loki_readonly_proxy.py"


class FakeLoki(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def do_GET(self) -> None:  # noqa: N802
        FakeLoki.requests.append((self.path, self.command))
        if self.path.startswith("/ready"):
            body = b"ready\n"
        else:
            body = json.dumps({"status": "success", "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_fake_loki() -> tuple[http.server.ThreadingHTTPServer, int]:
    FakeLoki.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeLoki)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def start_proxy(upstream_port: int) -> tuple[subprocess.Popen[str], int]:
    probe = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    port = probe.server_address[1]
    probe.server_close()

    proc = subprocess.Popen(
        [
            sys.executable,
            str(PROXY),
            "--bind-ip",
            "127.0.0.1",
            "--bind-port",
            str(port),
            "--upstream",
            f"http://127.0.0.1:{upstream_port}",
            "--default-limit",
            "1000",
            "--max-limit",
            "10000",
            "--default-window",
            "1h",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=0.2).read()  # noqa: S310
            return proc, port
        except OSError:
            time.sleep(0.05)
    stdout, stderr = proc.communicate(timeout=1)
    raise AssertionError(f"proxy did not start\nstdout={stdout}\nstderr={stderr}")


def request(url: str, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=2) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def with_proxy(test_fn) -> None:
    fake, fake_port = start_fake_loki()
    proc, proxy_port = start_proxy(fake_port)
    try:
        test_fn(proxy_port)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        fake.shutdown()
        fake.server_close()


def test_allows_known_read_endpoint() -> None:
    def run(proxy_port: int) -> None:
        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/labels")
        assert status == 200
        assert b'"status": "success"' in body

    with_proxy(run)


def test_rejects_push_and_write_methods() -> None:
    def run(proxy_port: int) -> None:
        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/push", method="POST")
        assert status == 405
        assert b"rejected POST /loki/api/v1/push" in body

        status, _body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/labels", method="DELETE")
        assert status == 405

    with_proxy(run)


def test_rejects_unknown_path() -> None:
    def run(proxy_port: int) -> None:
        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/delete")
        assert status == 404
        assert b"rejected path" in body

    with_proxy(run)


def test_rejects_excessive_limit() -> None:
    def run(proxy_port: int) -> None:
        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}&limit=10001")
        assert status == 400
        assert b"exceeds max limit" in body

    with_proxy(run)


def test_rejects_duplicate_negative_and_empty_limits() -> None:
    def run(proxy_port: int) -> None:
        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}&limit=1000000&limit=1")
        assert status == 400
        assert b"limit may be supplied only once" in body

        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}&limit=-5")
        assert status == 400
        assert b"limit must be greater than zero" in body

        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}&limit=")
        assert status == 400
        assert b"limit cannot be empty" in body

    with_proxy(run)


def test_injects_limit_and_range_bounds() -> None:
    def run(proxy_port: int) -> None:
        status, _body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}")
        assert status == 200
        path, method = FakeLoki.requests[-1]
        assert method == "GET"
        parsed = urllib.parse.urlsplit(path)
        query = urllib.parse.parse_qs(parsed.query)
        assert query["limit"] == ["1000"]
        assert "start" in query
        assert "end" in query

    with_proxy(run)


def test_since_counts_as_bounded_range() -> None:
    def run(proxy_port: int) -> None:
        status, _body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}&since=30m")
        assert status == 200
        path, _method = FakeLoki.requests[-1]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        assert query["since"] == ["30m"]
        assert query["limit"] == ["1000"]
        assert "start" not in query
        assert "end" not in query

    with_proxy(run)


def test_rejects_blank_and_huge_since() -> None:
    def run(proxy_port: int) -> None:
        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}&since=")
        assert status == 400
        assert b"since cannot be empty" in body

        status, body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}&since=999999h")
        assert status == 400
        assert b"exceeds max window" in body

    with_proxy(run)


def test_validates_explicit_start_end_range() -> None:
    def run(proxy_port: int) -> None:
        status, body = request(
            f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range"
            '?query={service="backend"}&start=0&end=9999999999999999999&limit=1000'
        )
        assert status == 400
        assert b"start/end range exceeds max window" in body

        status, body = request(
            f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range"
            '?query={service="backend"}&start=&end=1700000000'
        )
        assert status == 400
        assert b"start cannot be empty" in body

        status, body = request(
            f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range"
            '?query={service="backend"}&start=1700000000&start=1700000001'
        )
        assert status == 400
        assert b"start may be supplied only once" in body

        status, body = request(
            f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range"
            '?query={service="backend"}&since=30m&start=1700000000'
        )
        assert status == 400
        assert b"since cannot be combined with start or end" in body

    with_proxy(run)


def test_head_only_allowed_for_ready() -> None:
    def run(proxy_port: int) -> None:
        status, _body = request(f"http://127.0.0.1:{proxy_port}/ready", method="HEAD")
        assert status == 200

        status, _body = request(f"http://127.0.0.1:{proxy_port}/loki/api/v1/query_range?query={{service=\"backend\"}}", method="HEAD")
        assert status == 405

    with_proxy(run)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("symphony_prod_loki_readonly_proxy tests passed")
