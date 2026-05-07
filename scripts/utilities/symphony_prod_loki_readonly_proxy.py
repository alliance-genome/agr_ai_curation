#!/usr/bin/env python3
"""Tiny read-only Loki proxy for Symphony production log access."""

from __future__ import annotations

import argparse
import datetime
import http.server
import json
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping


ALLOWED_EXACT_PATHS = {
    "/ready",
    "/loki/api/v1/status/buildinfo",
    "/loki/api/v1/labels",
    "/loki/api/v1/series",
    "/loki/api/v1/query",
    "/loki/api/v1/query_range",
}
ALLOWED_LABEL_VALUES_RE = re.compile(r"^/loki/api/v1/label/[^/]+/values$")
LIMITED_PATHS = {
    "/loki/api/v1/series",
    "/loki/api/v1/query",
    "/loki/api/v1/query_range",
}
RANGE_PATHS = {
    "/loki/api/v1/series",
    "/loki/api/v1/query_range",
}


def parse_duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smhd])", value.strip())
    if not match:
        raise ValueError(f"invalid duration: {value}")
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return amount * multipliers[unit]


def normalize_path(path: str) -> str:
    decoded = urllib.parse.unquote(path)
    normalized = posixpath.normpath(decoded)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def single_param(params: dict[str, list[str]], name: str) -> tuple[str | None, int | None, str | None]:
    values = params.get(name, [])
    if len(values) > 1:
        return None, 400, f"{name} may be supplied only once"
    if not values:
        return None, None, None
    if values[0] == "":
        return None, 400, f"{name} cannot be empty"
    return values[0], None, None


def parse_loki_time_ns(value: str) -> int:
    if re.fullmatch(r"\d+", value):
        parsed = int(value)
        if parsed < 0:
            raise ValueError("time cannot be negative")
        if parsed > 100_000_000_000_000:
            return parsed
        return parsed * 1_000_000_000

    if re.fullmatch(r"\d+\.\d+", value):
        parsed_float = float(value)
        if parsed_float < 0:
            raise ValueError("time cannot be negative")
        return int(parsed_float * 1_000_000_000)

    iso_value = value.replace("Z", "+00:00")
    parsed_dt = datetime.datetime.fromisoformat(iso_value)
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=datetime.timezone.utc)
    return int(parsed_dt.timestamp() * 1_000_000_000)


def is_allowed_path(path: str) -> bool:
    return path in ALLOWED_EXACT_PATHS or bool(ALLOWED_LABEL_VALUES_RE.fullmatch(path))


def json_bytes(status: int, message: str) -> bytes:
    return json.dumps({"error": message, "status": status}, sort_keys=True).encode("utf-8")


def adjust_query(
    path: str,
    query: str,
    *,
    default_limit: int,
    max_limit: int,
    default_window: str,
    max_window: str,
) -> tuple[str, int | None, str | None]:
    params = urllib.parse.parse_qs(query, keep_blank_values=True)

    if path in LIMITED_PATHS:
        limit_value, error_status, error_message = single_param(params, "limit")
        if error_status is not None:
            return query, error_status, error_message
        if limit_value:
            try:
                limit = int(limit_value)
            except ValueError:
                return query, 400, "limit must be an integer"
            if limit <= 0:
                return query, 400, "limit must be greater than zero"
            if limit > max_limit:
                return query, 400, f"limit {limit} exceeds max limit {max_limit}"
        else:
            params["limit"] = [str(default_limit)]

    if path in RANGE_PATHS:
        now_ns = int(time.time() * 1_000_000_000)
        default_window_ns = parse_duration_seconds(default_window) * 1_000_000_000
        max_window_ns = parse_duration_seconds(max_window) * 1_000_000_000
        since_value, error_status, error_message = single_param(params, "since")
        if error_status is not None:
            return query, error_status, error_message
        start_value, error_status, error_message = single_param(params, "start")
        if error_status is not None:
            return query, error_status, error_message
        end_value, error_status, error_message = single_param(params, "end")
        if error_status is not None:
            return query, error_status, error_message

        if since_value and (start_value or end_value):
            return query, 400, "since cannot be combined with start or end"

        if since_value:
            try:
                since_seconds = parse_duration_seconds(since_value)
            except ValueError:
                return query, 400, "since must be a duration like 30m, 2h, or 1d"
            max_window_seconds = parse_duration_seconds(max_window)
            if since_seconds > max_window_seconds:
                return query, 400, f"since {since_value} exceeds max window {max_window}"
        else:
            try:
                end_ns = parse_loki_time_ns(end_value) if end_value else now_ns
                start_ns = parse_loki_time_ns(start_value) if start_value else end_ns - default_window_ns
            except (TypeError, ValueError):
                return query, 400, "start and end must be Loki timestamps"
            if end_ns < start_ns:
                return query, 400, "end must be greater than or equal to start"
            if end_ns - start_ns > max_window_ns:
                return query, 400, f"start/end range exceeds max window {max_window}"
            if not end_value:
                params["end"] = [str(end_ns)]
            if not start_value:
                params["start"] = [str(start_ns)]

    return urllib.parse.urlencode(params, doseq=True), None, None


class ReadOnlyLokiProxy(http.server.BaseHTTPRequestHandler):
    server_version = "SymphonyReadOnlyLokiProxy/1.0"

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = normalize_path(parsed.path)
        if path != "/ready":
            self._send_json_error(405, f"read-only Loki proxy rejected HEAD {path}")
            return
        self._handle_request(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._handle_request(send_body=True)

    def do_POST(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_method()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_method()

    def _reject_method(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = normalize_path(parsed.path)
        self._send_json_error(405, f"read-only Loki proxy rejected {self.command} {path}")

    def _handle_request(self, *, send_body: bool) -> None:
        started = time.monotonic()
        parsed = urllib.parse.urlsplit(self.path)
        path = normalize_path(parsed.path)

        if not is_allowed_path(path):
            self._send_json_error(404, f"read-only Loki proxy rejected path {path}")
            return

        query, error_status, error_message = adjust_query(
            path,
            parsed.query,
            default_limit=self.server.default_limit,  # type: ignore[attr-defined]
            max_limit=self.server.max_limit,  # type: ignore[attr-defined]
            default_window=self.server.default_window,  # type: ignore[attr-defined]
            max_window=self.server.max_window,  # type: ignore[attr-defined]
        )
        if error_status is not None:
            self._send_json_error(error_status, error_message or "invalid query")
            return

        upstream_url = urllib.parse.urljoin(self.server.upstream, path)  # type: ignore[attr-defined]
        if query:
            upstream_url = f"{upstream_url}?{query}"

        request = urllib.request.Request(upstream_url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.server.timeout_seconds) as response:  # noqa: S310
                body = response.read() if send_body else b""
                self.send_response(response.status)
                self._copy_headers(response.headers)
                self.end_headers()
                if send_body:
                    self.wfile.write(body)
                self._log_request(started, path, response.status, len(parsed.query))
        except urllib.error.HTTPError as exc:
            body = exc.read() if send_body else b""
            self.send_response(exc.code)
            self._copy_headers(exc.headers)
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            self._log_request(started, path, exc.code, len(parsed.query))
        except TimeoutError:
            self._send_json_error(504, "upstream Loki request timed out")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            self._send_json_error(502, f"upstream Loki request failed: {reason}")

    def _copy_headers(self, headers: Mapping[str, str]) -> None:
        blocked = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
        for key, value in headers.items():
            if key.lower() not in blocked:
                self.send_header(key, value)

    def _send_json_error(self, status: int, message: str) -> None:
        body = json_bytes(status, message)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.log_message("%s", message)

    def _log_request(self, started: float, path: str, status: int, query_length: int) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        self.log_message("path=%s status=%s duration_ms=%s query_length=%s", path, status, duration_ms, query_length)


class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expose a read-only subset of Loki HTTP APIs.")
    parser.add_argument("--bind-ip", required=True)
    parser.add_argument("--bind-port", type=int, default=43100)
    parser.add_argument("--upstream", default="http://127.0.0.1:43101")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--default-limit", type=int, default=1000)
    parser.add_argument("--max-limit", type=int, default=10000)
    parser.add_argument("--default-window", default="1h")
    parser.add_argument("--max-window", default="24h")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        parse_duration_seconds(args.default_window)
        parse_duration_seconds(args.max_window)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    server = ThreadingHTTPServer((args.bind_ip, args.bind_port), ReadOnlyLokiProxy)
    server.upstream = args.upstream.rstrip("/")  # type: ignore[attr-defined]
    server.timeout_seconds = args.timeout_seconds  # type: ignore[attr-defined]
    server.default_limit = args.default_limit  # type: ignore[attr-defined]
    server.max_limit = args.max_limit  # type: ignore[attr-defined]
    server.default_window = args.default_window  # type: ignore[attr-defined]
    server.max_window = args.max_window  # type: ignore[attr-defined]
    print(f"read-only Loki proxy listening on {args.bind_ip}:{args.bind_port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
