"""Demand-triggered, application-lifecycle-owned connection health snapshots."""
import asyncio
import copy
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.lib.config import connections_loader as loader
from src.lib.observability.runtime import report_runtime_exception
from src.lib.openai_agents.config import get_connections_health_snapshot_ttl_seconds


class SnapshotUnavailable(RuntimeError):
    """No complete current-generation observation can be returned."""


@dataclass(frozen=True)
class HealthSnapshot:
    observed_at: str
    services: dict
    generation: int
    expires_at: float


class ConnectionsHealthMonitor:
    """One pending sweep per generation and one completed snapshot per worker.

    Construct and close inside the app lifespan. All waiter cancellation is
    shielded; the owner retains tasks and retrieves their exceptions.
    """
    def __init__(self):
        self._loop = asyncio.get_running_loop()
        self._snapshot = None
        self._pending = {}
        self._tasks = set()
        self._closed = False

    async def get(self) -> HealthSnapshot:
        if self._closed or asyncio.get_running_loop() is not self._loop:
            raise SnapshotUnavailable("Connection monitor is not running")
        generation = loader.connection_generation()
        snapshot = self._snapshot
        if snapshot is not None and snapshot.generation == generation and time.monotonic() < snapshot.expires_at:
            return copy.deepcopy(snapshot)
        task = self._pending.get(generation)
        if task is None:
            generation, connections = loader.copy_connection_generation()
            task = asyncio.create_task(self._sweep(generation, connections))
            self._pending[generation] = task
            self._tasks.add(task)
            task.add_done_callback(self._finished)
        result = await asyncio.shield(task)
        if generation != loader.connection_generation():
            raise SnapshotUnavailable("Connection configuration changed during observation")
        return copy.deepcopy(result)

    def _finished(self, task):
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _sweep(self, generation, connections):
        started = time.monotonic()
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            ttl = get_connections_health_snapshot_ttl_seconds()
            services = await loader.check_all_health(connections)
            if generation != loader.connection_generation():
                raise SnapshotUnavailable("Connection configuration changed during observation")
            snapshot = HealthSnapshot(
                observed_at, services,
                generation, started + ttl,
            )
            self._snapshot = snapshot
            return snapshot
        except SnapshotUnavailable:
            raise
        except Exception:
            # Report once per sweep, including sweeps whose waiters disconnected.
            # Never send raw probe exceptions, URLs or credentials to telemetry.
            report_runtime_exception(
                RuntimeError("Connection monitoring sweep failed"),
                component="connections_monitor", operation="sweep",
            )
            raise SnapshotUnavailable("Connection monitoring sweep did not complete") from None
        finally:
            self._pending.pop(generation, None)

    async def close(self):
        self._closed = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._pending.clear()
        self._snapshot = None
