"""Subprocess package tool runner backed by per-package virtual environments."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .env_manager import (
    PackageEnvironmentBootstrapError,
    PackageEnvironmentManager,
)
from .runner_protocol import (
    PROTOCOL_VERSION,
    RunnerErrorResponse,
    RunnerRequest,
    decode_response,
    encode_request,
)
from .tool_registry import ToolRegistry, load_tool_registry


@dataclass(frozen=True)
class PackageRunnerError:
    """Structured failure returned by the package tool runner."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PackageToolExecutionResult:
    """Outcome of one isolated package tool execution."""

    ok: bool
    result: Any = None
    error: PackageRunnerError | None = None
    stdout: str = ""
    stderr: str = ""
    environment_reused: bool | None = None


class PackageToolRunner:
    """Execute package-declared tools through an isolated subprocess."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry | None = None,
        env_manager: PackageEnvironmentManager | None = None,
        entrypoint_path: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._tool_registry = tool_registry or load_tool_registry()
        self._env_manager = env_manager or PackageEnvironmentManager()
        self._entrypoint_path = (
            entrypoint_path
            or Path(__file__).resolve(strict=False).with_name(
                "package_runner_entrypoint.py"
            )
        )
        self._timeout_seconds = timeout_seconds

    def execute_tool(
        self,
        tool_id: str,
        *,
        args: Sequence[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> PackageToolExecutionResult:
        """Execute one package tool and return a structured success or failure."""
        binding = self._tool_registry.get(tool_id)
        if binding is None:
            return PackageToolExecutionResult(
                ok=False,
                error=PackageRunnerError(
                    code="tool_not_found",
                    message=f"Unknown package tool binding '{tool_id}'",
                ),
            )

        package = self._tool_registry.package_registry.get_package(binding.source.package_id)
        if package is None:
            return PackageToolExecutionResult(
                ok=False,
                error=PackageRunnerError(
                    code="tool_not_found",
                    message=(
                        f"Package '{binding.source.package_id}' is not available for tool '{tool_id}'"
                    ),
                ),
            )

        try:
            environment = self._env_manager.ensure_environment(package)
        except PackageEnvironmentBootstrapError as exc:
            return PackageToolExecutionResult(
                ok=False,
                error=PackageRunnerError(
                    code="bootstrap_failure",
                    message=str(exc),
                    details=exc.details,
                ),
            )

        request = RunnerRequest(
            protocol_version=PROTOCOL_VERSION,
            package_id=package.package_id,
            package_version=package.version,
            package_root=str(package.package_path),
            python_package_root=package.manifest.python_package_root,
            tool_id=tool_id,
            import_path=binding.import_path,
            import_attribute_kind=binding.import_attribute_kind,
            binding_kind=binding.binding_kind.value,
            required_context=list(binding.required_context),
            context=dict(context or {}),
            args=list(args or []),
            kwargs=dict(kwargs or {}),
        )

        try:
            completed = subprocess.run(
                [str(environment.python_executable), str(self._entrypoint_path)],
                check=False,
                capture_output=True,
                text=True,
                input=encode_request(request),
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return PackageToolExecutionResult(
                ok=False,
                error=PackageRunnerError(
                    code="execution_failure",
                    message=f"Timed out while executing package tool '{tool_id}'",
                    details={"timeout_seconds": self._timeout_seconds},
                ),
                environment_reused=environment.reused,
            )

        try:
            response = decode_response(completed.stdout)
        except ValueError as exc:
            return PackageToolExecutionResult(
                ok=False,
                error=PackageRunnerError(
                    code="bad_runner_response",
                    message=str(exc),
                    details={
                        "returncode": completed.returncode,
                        "stdout": completed.stdout.strip(),
                        "stderr": completed.stderr.strip(),
                    },
                ),
                stdout=completed.stdout,
                stderr=completed.stderr,
                environment_reused=environment.reused,
            )

        if isinstance(response, RunnerErrorResponse):
            return PackageToolExecutionResult(
                ok=False,
                error=PackageRunnerError(
                    code=response.error.code,
                    message=response.error.message,
                    details=response.error.details,
                ),
                stdout=completed.stdout,
                stderr=completed.stderr,
                environment_reused=environment.reused,
            )

        if completed.returncode != 0:
            return PackageToolExecutionResult(
                ok=False,
                error=PackageRunnerError(
                    code="bad_runner_response",
                    message=(
                        f"Runner exited with code {completed.returncode} despite success payload"
                    ),
                    details={
                        "returncode": completed.returncode,
                        "stdout": completed.stdout.strip(),
                        "stderr": completed.stderr.strip(),
                    },
                ),
                stdout=completed.stdout,
                stderr=completed.stderr,
                environment_reused=environment.reused,
            )

        return PackageToolExecutionResult(
            ok=True,
            result=response.result,
            stdout=completed.stdout,
            stderr=completed.stderr,
            environment_reused=environment.reused,
        )


def execute_package_tool(
    tool_id: str,
    *,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    runner: PackageToolRunner | None = None,
) -> PackageToolExecutionResult:
    """Convenience wrapper for one-off package tool execution."""
    active_runner = runner or PackageToolRunner()
    return active_runner.execute_tool(
        tool_id,
        args=args,
        kwargs=kwargs,
        context=context,
    )
