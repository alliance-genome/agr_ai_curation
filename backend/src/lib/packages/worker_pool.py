"""Bounded process reuse for the isolated package-tool JSON protocol."""

from __future__ import annotations

import os
import selectors
import subprocess
import threading
import time
from dataclasses import dataclass

from .runner_protocol import decode_response


@dataclass(eq=False)
class _Worker:
    key: tuple[str, ...]
    process: subprocess.Popen
    busy: bool = False

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe is not None:
                pipe.close()


class PackageWorkerPool:
    """One request per worker; never replay a failed request automatically."""

    def __init__(self, *, capacity: int, response_max_bytes: int) -> None:
        self._capacity = capacity
        self._response_max_bytes = response_max_bytes
        self._condition = threading.Condition()
        self._workers: list[_Worker] = []
        self._closed = False

    def close(self) -> None:
        with self._condition:
            self._closed = True
            # Active exchanges observe EOF and return a failure after termination.
            for worker in self._workers:
                if worker.process.poll() is None:
                    worker.process.kill()
                if not worker.busy:
                    worker.close()
            self._workers = [w for w in self._workers if w.busy]
            self._condition.notify_all()

    def execute(
        self, *, key: tuple[str, ...], command: list[str], payload: str, timeout: float,
    ) -> subprocess.CompletedProcess:
        deadline = time.monotonic() + timeout
        worker = self._acquire(key, command, deadline, timeout)
        healthy = False
        try:
            stdout, stderr = self._exchange(worker, payload, deadline, timeout)
            decode_response(stdout)
            healthy = True
            return subprocess.CompletedProcess(command, 0, stdout, stderr)
        finally:
            with self._condition:
                worker.busy = False
                if not healthy or self._closed or worker.process.poll() is not None:
                    worker.close()
                    self._workers.remove(worker)
                self._condition.notify_all()

    def _acquire(self, key, command, deadline, timeout) -> _Worker:
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("Package worker pool is closed")
                for worker in list(self._workers):
                    if not worker.busy and worker.process.poll() is not None:
                        worker.close()
                        self._workers.remove(worker)
                for worker in self._workers:
                    if not worker.busy and worker.key == key:
                        worker.busy = True
                        return worker
                if len(self._workers) >= self._capacity:
                    idle = next((w for w in self._workers if not w.busy), None)
                    if idle is not None:
                        idle.close()
                        self._workers.remove(idle)
                if len(self._workers) < self._capacity:
                    process = subprocess.Popen(
                        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, bufsize=0,
                    )
                    worker = _Worker(key, process, busy=True)
                    self._workers.append(worker)
                    return worker
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                self._condition.wait(remaining)

    def _exchange(self, worker, payload, deadline, timeout) -> tuple[str, str]:
        process = worker.process
        pending = memoryview((payload + "\n").encode("utf-8"))
        stdout = bytearray()
        stderr = bytearray()
        with selectors.DefaultSelector() as selector:
            for pipe, event, name in (
                (process.stdin, selectors.EVENT_WRITE, "stdin"),
                (process.stdout, selectors.EVENT_READ, "stdout"),
                (process.stderr, selectors.EVENT_READ, "stderr"),
            ):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, event, name)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                for selected, _ in selector.select(remaining):
                    fd = selected.fd
                    if selected.data == "stdin":
                        try:
                            written = os.write(fd, pending)
                        except BlockingIOError:
                            continue
                        pending = pending[written:]
                        if not pending:
                            selector.unregister(selected.fileobj)
                        continue
                    try:
                        chunk = os.read(fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        if selected.data == "stdout":
                            raise RuntimeError("Package worker exited before returning a response")
                        selector.unregister(selected.fileobj)
                        continue
                    buffer = stdout if selected.data == "stdout" else stderr
                    buffer.extend(chunk)
                    if len(stdout) + len(stderr) > self._response_max_bytes:
                        raise ValueError("Package worker response exceeds the configured byte limit")
                    if selected.data == "stdout" and b"\n" in stdout:
                        line, extra = bytes(stdout).split(b"\n", 1)
                        if extra or pending:
                            raise ValueError("Unexpected data in package worker response")
                        # Drain diagnostics already written before the response,
                        # so they cannot be attributed to the next caller.
                        while True:
                            try:
                                diagnostic = os.read(process.stderr.fileno(), 65536)
                            except BlockingIOError:
                                break
                            if not diagnostic:
                                break
                            stderr.extend(diagnostic)
                            if len(stdout) + len(stderr) > self._response_max_bytes:
                                raise ValueError("Package worker response exceeds the configured byte limit")
                        return line.decode("utf-8"), stderr.decode("utf-8", errors="replace")
