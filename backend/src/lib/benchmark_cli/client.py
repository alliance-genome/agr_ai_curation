"""Bounded, non-retrying HTTP transport with explicit credential channels."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.lib.openai_agents.config import (
    get_benchmark_cli_max_response_bytes,
    get_benchmark_cli_request_timeout_seconds,
)


class ExitCode(IntEnum):
    OK = 0
    VALIDATION = 2
    AUTHORIZATION = 3
    CONFLICT = 4
    PARTIAL_FAILURE = 5
    CANCELLED = 6
    TRANSPORT = 7
    SERVER = 8


class ClientError(Exception):
    """Only fixed, safe messages may cross the process-error boundary."""

    def __init__(self, code: ExitCode, message: str, *, resume_after: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.resume_after = resume_after


@dataclass(frozen=True, repr=False)
class Credentials:
    access: str
    curator: str | None = None
    source: str | None = None

    def headers(self, *, human: bool = False, source: bool = False) -> dict[str, str]:
        selected: list[tuple[str, str | None]] = [("Authorization", self.access)]
        if human:
            selected.append(("X-Benchmark-Curator-Authorization", self.curator))
        if source:
            selected.append(("X-Benchmark-Delegated-Source-Authorization", self.source))
        result = {"Accept": "application/json"}
        for name, value in selected:
            if not value or not value.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value):
                raise ClientError(ExitCode.AUTHORIZATION, "Required credential is missing or malformed")
            result[name] = f"Bearer {value}"
        return result


def validate_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ClientError(ExitCode.VALIDATION, "Invalid API base URL") from None
    if (
        parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
        or not parsed.hostname or any(c.isspace() or ord(c) < 32 for c in value)
        or not (parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ))
    ):
        raise ClientError(ExitCode.VALIDATION, "Use an HTTPS API origin (HTTP is allowed only for loopback)")
    return value.rstrip("/")


class BenchmarkClient:
    def __init__(self, base_url: str, credentials: Credentials, *, transport: httpx.BaseTransport | None = None):
        self.base_url = validate_base_url(base_url)
        self.credentials = credentials
        timeout = get_benchmark_cli_request_timeout_seconds()
        if not math.isfinite(timeout) or timeout <= 0:
            raise ClientError(ExitCode.VALIDATION, "CLI timeout must be finite and positive")
        self.http = httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=transport,
        )

    def __enter__(self) -> BenchmarkClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.http.close()

    def events(self, job_id: str, cursor: str | None = None):
        """Yield decoded SSE frames; transport errors never mutate server work."""
        from uuid import UUID

        job_id = str(UUID(job_id))
        headers = self.credentials.headers()
        headers["Accept"] = "text/event-stream"
        if cursor is not None:
            if not re.fullmatch(re.escape(job_id) + r":[0-9]+", cursor):
                raise ClientError(ExitCode.VALIDATION, "Invalid event cursor")
            headers["Last-Event-ID"] = cursor
        limit = get_benchmark_cli_max_response_bytes()
        try:
            with self.http.stream("GET", f"{self.base_url}/api/v1/benchmarks/jobs/{job_id}/events", headers=headers) as response:
                if response.status_code == 410:
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=limit):
                        if len(raw) + len(chunk) > limit:
                            raise ClientError(ExitCode.SERVER, "API response exceeds the configured byte limit")
                        raw.extend(chunk)
                    try:
                        detail = json.loads(raw)["detail"]
                        resume = detail["resume_after"]
                        if detail["code"] != "event_history_expired" or not isinstance(resume, str):
                            raise ValueError
                    except (ValueError, KeyError, TypeError):
                        raise ClientError(ExitCode.SERVER, "Invalid event recovery response") from None
                    raise ClientError(ExitCode.CONFLICT, "Event history expired", resume_after=resume)
                if response.status_code in {400, 401, 403, 404, 409, 422}:
                    code = {401: ExitCode.AUTHORIZATION, 403: ExitCode.AUTHORIZATION, 409: ExitCode.CONFLICT}.get(response.status_code, ExitCode.VALIDATION)
                    raise ClientError(code, f"Event observation rejected (HTTP {response.status_code})")
                if response.status_code != 200 or response.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream":
                    raise ClientError(ExitCode.SERVER, "Event stream unavailable")
                pending = bytearray()
                frame_size = 0
                event, identifier, data = "message", None, []
                # Do not coalesce to the response limit: live frames must arrive
                # immediately, not only after 10 MiB or upstream EOF.
                for chunk in response.iter_bytes():
                    pending.extend(chunk)
                    while b"\n" in pending:
                        raw_line, _, remainder = pending.partition(b"\n")
                        pending = bytearray(remainder)
                        frame_size += len(raw_line) + 1
                        if frame_size > limit:
                            raise ClientError(ExitCode.SERVER, "Event frame exceeds the configured byte limit")
                        try:
                            line = raw_line.rstrip(b"\r").decode("utf-8")
                        except UnicodeDecodeError:
                            raise ClientError(ExitCode.SERVER, "Invalid event frame") from None
                        if not line:
                            if data:
                                try:
                                    payload = json.loads("\n".join(data))
                                except ValueError:
                                    raise ClientError(ExitCode.SERVER, "Invalid event JSON") from None
                                yield event, identifier, payload
                            event, identifier, data, frame_size = "message", None, [], 0
                        elif not line.startswith(":"):
                            field, _, value = line.partition(":")
                            value = value.removeprefix(" ")
                            if field == "event":
                                event = value
                            elif field == "id":
                                identifier = value
                            elif field == "data":
                                data.append(value)
                    if frame_size + len(pending) > limit:
                        raise ClientError(ExitCode.SERVER, "Event frame exceeds the configured byte limit")
        except httpx.HTTPError:
            raise ClientError(ExitCode.TRANSPORT, "Event connection interrupted") from None

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        body: Any = None, human: bool = False, source: bool = False,
        idempotency_key: str | None = None,
    ) -> Any:
        # Paths are generated by commands, never redirects or supplied next-page URLs.
        if not re.fullmatch(r"/api/v1/benchmarks(?:/[A-Za-z0-9_.:-]+)*", path) or any(
            part in {".", ".."} for part in path.split("/")
        ):
            raise ClientError(ExitCode.VALIDATION, "Invalid benchmark API path")
        headers = self.credentials.headers(human=human, source=source)
        if idempotency_key is not None:
            if not re.fullmatch(r"[!-~]{1,255}", idempotency_key):
                raise ClientError(ExitCode.VALIDATION, "Invalid idempotency key")
            headers["Idempotency-Key"] = idempotency_key
        try:
            content = None if body is None else json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError):
            raise ClientError(ExitCode.VALIDATION, "Request must contain valid JSON") from None
        if content is not None:
            headers["Content-Type"] = "application/json"
        try:
            with self.http.stream(method, self.base_url + path, headers=headers, params=params, content=content) as response:
                if not response.is_success:
                    code = {
                        400: ExitCode.VALIDATION, 401: ExitCode.AUTHORIZATION,
                        403: ExitCode.AUTHORIZATION, 404: ExitCode.VALIDATION,
                        409: ExitCode.CONFLICT, 413: ExitCode.VALIDATION,
                        415: ExitCode.VALIDATION, 422: ExitCode.VALIDATION,
                    }.get(response.status_code, ExitCode.SERVER)
                    # Never echo server errors: proxies/validators may include private bodies.
                    raise ClientError(code, f"Benchmark API rejected request (HTTP {response.status_code})")
                if response.status_code == 204:
                    return None
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > get_benchmark_cli_max_response_bytes():
                        raise ClientError(ExitCode.SERVER, "API response exceeds the configured byte limit")
                    data.extend(chunk)
                try:
                    return json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    raise ClientError(ExitCode.SERVER, "API returned invalid JSON") from None
        except httpx.HTTPError:
            # In particular, do not replay POST after response loss. Caller keeps key/body.
            raise ClientError(ExitCode.TRANSPORT, "API connection interrupted; submission outcome may be unknown") from None
