#!/usr/bin/env python3
"""Fixed host control endpoint for the Symphony production Loki tunnel."""

from __future__ import annotations

import argparse
import http.server
import json
import pathlib
import subprocess
import time


class ControlHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SymphonyProdLokiControl/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/prod-loki-tunnel/status":
            self._json(404, {"error": "not_found"})
            return
        output, status = self._run(["bash", str(self.server.status_script), "--json"])  # type: ignore[attr-defined]
        payload = self._decode_json(output, {"status": "unknown", "raw_output": output.strip()})
        self._json(200 if status == 0 else 503, payload)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/prod-loki-tunnel/repair":
            self._json(404, {"error": "not_found"})
            return
        output, status = self._run(["bash", str(self.server.repair_script)])  # type: ignore[attr-defined]
        self._json(200 if status == 0 else 503, {"status": "ok" if status == 0 else "failed", "output": output[-4000:]})

    def do_PUT(self) -> None:  # noqa: N802
        self._reject()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject()

    def _reject(self) -> None:
        self._json(405, {"error": "method_not_allowed"})

    def _run(self, cmd: list[str]) -> tuple[str, int]:
        try:
            result = subprocess.run(
                cmd,
                cwd=self.server.repo_root,  # type: ignore[attr-defined]
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.server.command_timeout,  # type: ignore[attr-defined]
                check=False,
            )
            return result.stdout, result.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return f"{output}\ncommand timed out", 124

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _decode_json(self, output: str, fallback: dict) -> dict:
        try:
            decoded = json.loads(output)
            return decoded if isinstance(decoded, dict) else fallback
        except json.JSONDecodeError:
            return fallback

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {self.client_address[0]} {fmt % args}", flush=True)


class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Expose fixed production Loki tunnel control actions to Symphony.")
    parser.add_argument("--bind-ip", required=True)
    parser.add_argument("--bind-port", type=int, default=43102)
    parser.add_argument("--repo-root", default=str(pathlib.Path(__file__).resolve().parents[2]))
    parser.add_argument("--command-timeout", type=float, default=60)
    args = parser.parse_args()

    repo_root = pathlib.Path(args.repo_root).resolve()
    server = ThreadingHTTPServer((args.bind_ip, args.bind_port), ControlHandler)
    server.repo_root = repo_root  # type: ignore[attr-defined]
    server.status_script = repo_root / "scripts" / "utilities" / "symphony_prod_loki_host_status.sh"  # type: ignore[attr-defined]
    server.repair_script = repo_root / "scripts" / "utilities" / "symphony_prod_loki_host_repair.sh"  # type: ignore[attr-defined]
    server.command_timeout = args.command_timeout  # type: ignore[attr-defined]
    print(f"production Loki control server listening on {args.bind_ip}:{args.bind_port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
