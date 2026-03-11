"""Stdlib-only worker entrypoint for isolated package tool execution."""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from runner_protocol import (  # type: ignore[import-not-found]
    RunnerError,
    RunnerProtocolError,
    decode_request,
    encode_error_response,
    encode_success_response,
)


def main() -> int:
    try:
        request = decode_request(sys.stdin.read())
        tool_callable = _resolve_tool_callable(request)
        result = tool_callable(*request.args, **request.kwargs)
        json.dumps(result)
        sys.stdout.write(encode_success_response(result))
        return 0
    except RunnerProtocolError as exc:
        sys.stdout.write(
            encode_error_response(
                RunnerError(
                    code="invalid_request",
                    message=str(exc),
                )
            )
        )
        return 1
    except (ImportError, AttributeError) as exc:
        sys.stdout.write(
            encode_error_response(
                RunnerError(
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
            encode_error_response(
                RunnerError(
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


def _resolve_tool_callable(request):
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

    if not callable(imported):
        raise TypeError(f"Imported target '{request.import_path}' is not callable")
    return imported


def _extend_sys_path(request) -> None:
    package_root = Path(request.package_root).expanduser().resolve(strict=False)
    python_package_root = (
        package_root / request.python_package_root
    ).expanduser().resolve(strict=False)
    for candidate in (python_package_root.parent, python_package_root, package_root):
        candidate_text = str(candidate)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)


if __name__ == "__main__":
    raise SystemExit(main())
