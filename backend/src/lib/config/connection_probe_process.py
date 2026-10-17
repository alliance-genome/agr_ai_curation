"""Owned process boundary for monitoring probes with synchronous dependencies.

Input travels over stdin, never command-line arguments or temporary files.
The child uses the canonical probe and resolver implementations.
"""
import asyncio
import json
import os
import signal
import sys
from dataclasses import asdict

from src.lib.config import connections_loader as loader


async def probe_in_process(conn: loader.ConnectionDefinition) -> dict:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "src.lib.config.connection_probe_process",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        output, _ = await process.communicate(json.dumps(asdict(conn)).encode())
        if process.returncode != 0:
            raise RuntimeError("Isolated connection probe failed")
        return json.loads(output)
    finally:
        # Kill the whole process group, including external credential commands.
        # Reap even on timeout/cancellation; no abandoned executor threads.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def _main() -> None:
    data = json.load(sys.stdin)
    data.pop("effective_display_url")
    data["health_check"] = loader.HealthCheck(**data["health_check"])
    if data["credentials"] is not None:
        data["credentials"] = loader.CredentialsConfig(**data["credentials"])
    conn = loader.ConnectionDefinition(**data)
    # The canonical credential resolver reads the registry. Supply exactly the
    # captured definition, not a reload of possibly changed deployment files.
    loader._connection_registry = {conn.service_id: conn}
    loader._initialized = True
    await loader._check_connection_health(conn)
    print(json.dumps(loader.connection_status(conn)))


if __name__ == "__main__":
    asyncio.run(_main())
