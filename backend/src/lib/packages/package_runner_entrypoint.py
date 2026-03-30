"""Stdlib-only worker entrypoint for isolated package tool execution."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
HOST_RUNTIME_SRC_DIR = CURRENT_DIR.parent.parent
HOST_RUNTIME_ROOT_DIR = HOST_RUNTIME_SRC_DIR.parent


def main() -> int:
    # Package tool calls already run in an isolated subprocess, so downstream
    # helpers can safely skip extra worker-thread offloading.
    os.environ["AGR_AI_CURATION_PACKAGE_TOOL_SUBPROCESS"] = "1"
    protocol = _load_runner_protocol()
    try:
        request = protocol["decode_request"](sys.stdin.read())
        tool_target = _resolve_tool_target(request)
        result = _normalize_result(_execute_tool_target(tool_target, request))
        json.dumps(result)
        sys.stdout.write(protocol["encode_success_response"](result))
        return 0
    except protocol["RunnerProtocolError"] as exc:
        sys.stdout.write(
            protocol["encode_error_response"](
                protocol["RunnerError"](
                    code="invalid_request",
                    message=str(exc),
                )
            )
        )
        return 1
    except (ImportError, AttributeError) as exc:
        sys.stdout.write(
            protocol["encode_error_response"](
                protocol["RunnerError"](
                    code="import_failure",
                    message=str(exc),
                    details={
                        "exception_type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
            )
        )
        return 1
    except Exception as exc:  # pragma: no cover - exercised via subprocess tests
        sys.stdout.write(
            protocol["encode_error_response"](
                protocol["RunnerError"](
                    code="execution_failure",
                    message=str(exc),
                    details={
                        "exception_type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
            )
        )
        return 1


def _load_runner_protocol() -> dict[str, Any]:
    if str(CURRENT_DIR) not in sys.path:
        sys.path.insert(0, str(CURRENT_DIR))

    from runner_protocol import (  # type: ignore[import-not-found]
        RunnerError,
        RunnerProtocolError,
        decode_request,
        encode_error_response,
        encode_success_response,
    )

    return {
        "RunnerError": RunnerError,
        "RunnerProtocolError": RunnerProtocolError,
        "decode_request": decode_request,
        "encode_error_response": encode_error_response,
        "encode_success_response": encode_success_response,
    }


def _resolve_tool_target(request) -> Any:
    _extend_sys_path(request)
    module_name, attribute_name = request.import_path.split(":", 1)
    module = importlib.import_module(module_name)
    imported = getattr(module, attribute_name)

    if request.import_attribute_kind == "callable_factory":
        missing_context = [
            key
            for key in request.required_context
            if request.context.get(key) in (None, "")
        ]
        if missing_context:
            raise ValueError(
                f"Tool '{request.tool_id}' requires execution context: "
                + ", ".join(missing_context)
            )
        if not callable(imported):
            raise TypeError(
                f"Imported factory '{request.import_path}' is not callable"
            )
        imported = imported(dict(request.context))

    if not callable(imported) and not hasattr(imported, "on_invoke_tool"):
        raise TypeError(f"Imported target '{request.import_path}' is not callable")
    return imported


def _execute_tool_target(target: Any, request) -> Any:
    """Execute either a plain callable or an SDK-style tool object."""
    if hasattr(target, "on_invoke_tool"):
        payload: Any
        if request.kwargs:
            payload = request.kwargs
        elif request.args:
            payload = request.args
        else:
            payload = {}

        result = target.on_invoke_tool(
            SimpleNamespace(tool_name=request.tool_id),
            json.dumps(payload),
        )
    else:
        result = target(*request.args, **request.kwargs)

    # This entrypoint always runs in a fresh subprocess, so asyncio.run() is the
    # correct way to drive async tool objects without relying on a shared loop.
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _normalize_result(value: Any) -> Any:
    """Convert SDK/Pydantic return values into plain JSON-compatible objects."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _normalize_result(model_dump())
    if isinstance(value, dict):
        return {key: _normalize_result(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_result(item) for item in value]
    return value


def _extend_sys_path(request) -> None:
    package_root = Path(request.package_root).expanduser().resolve(strict=False)
    python_package_root = (
        package_root / request.python_package_root
    ).expanduser().resolve(strict=False)
    for candidate in (
        HOST_RUNTIME_SRC_DIR,
        python_package_root.parent,
        python_package_root,
        package_root,
    ):
        candidate_text = str(candidate)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)

    host_runtime_root_text = str(HOST_RUNTIME_ROOT_DIR)
    if host_runtime_root_text not in sys.path:
        # Keep the backend package root available for public runtime imports
        # without letting it outrank package-local or backend src modules.
        sys.path.append(host_runtime_root_text)


if __name__ == "__main__":
    raise SystemExit(main())
