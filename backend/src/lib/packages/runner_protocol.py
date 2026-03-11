"""JSON stdin/stdout protocol for isolated package tool execution."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

PROTOCOL_VERSION = "1.0"


class RunnerProtocolError(ValueError):
    """Raised when runner JSON cannot be parsed or validated."""


@dataclass(frozen=True)
class RunnerRequest:
    """One package tool execution request."""

    protocol_version: str
    package_id: str
    package_version: str
    package_root: str
    python_package_root: str
    tool_id: str
    import_path: str
    import_attribute_kind: str
    binding_kind: str
    required_context: list[str]
    context: dict[str, Any]
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunnerError:
    """Structured failure payload returned by the worker."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunnerSuccessResponse:
    """Successful tool execution response."""

    status: str
    result: Any


@dataclass(frozen=True)
class RunnerErrorResponse:
    """Failed tool execution response."""

    status: str
    error: RunnerError


def encode_request(request: RunnerRequest) -> str:
    """Serialize one execution request to JSON."""
    return json.dumps(asdict(request), sort_keys=True)


def decode_request(payload: str) -> RunnerRequest:
    """Parse and validate one execution request."""
    data = _decode_mapping(payload, expected_type="request")
    protocol_version = _require_string(data, "protocol_version")
    if protocol_version != PROTOCOL_VERSION:
        raise RunnerProtocolError(
            f"Unsupported runner protocol version '{protocol_version}'"
        )
    context = _require_mapping(data, "context")
    kwargs = _require_mapping(data, "kwargs")
    args = data.get("args", [])
    required_context = data.get("required_context", [])

    if not isinstance(args, list):
        raise RunnerProtocolError("request.args must be a JSON array")
    if not isinstance(required_context, list) or any(
        not isinstance(item, str) for item in required_context
    ):
        raise RunnerProtocolError(
            "request.required_context must be an array of strings"
        )

    return RunnerRequest(
        protocol_version=protocol_version,
        package_id=_require_string(data, "package_id"),
        package_version=_require_string(data, "package_version"),
        package_root=_require_string(data, "package_root"),
        python_package_root=_require_string(data, "python_package_root"),
        tool_id=_require_string(data, "tool_id"),
        import_path=_require_string(data, "import_path"),
        import_attribute_kind=_require_string(data, "import_attribute_kind"),
        binding_kind=_require_string(data, "binding_kind"),
        required_context=required_context,
        context=context,
        args=args,
        kwargs=kwargs,
    )


def encode_success_response(result: Any) -> str:
    """Serialize one successful execution response."""
    return json.dumps({"status": "ok", "result": result}, sort_keys=True)


def encode_error_response(error: RunnerError) -> str:
    """Serialize one failed execution response."""
    return json.dumps(
        {"status": "error", "error": asdict(error)},
        sort_keys=True,
    )


def decode_response(payload: str) -> RunnerSuccessResponse | RunnerErrorResponse:
    """Parse and validate one execution response."""
    data = _decode_mapping(payload, expected_type="response")
    status = _require_string(data, "status")
    if status == "ok":
        if "result" not in data:
            raise RunnerProtocolError("response.result is required when status is 'ok'")
        return RunnerSuccessResponse(status=status, result=data["result"])
    if status != "error":
        raise RunnerProtocolError(f"Unknown response status '{status}'")

    error = _require_mapping(data, "error")
    return RunnerErrorResponse(
        status=status,
        error=RunnerError(
            code=_require_string(error, "code", owner="response.error"),
            message=_require_string(error, "message", owner="response.error"),
            details=_optional_mapping(error, "details", owner="response.error"),
        ),
    )


def _decode_mapping(payload: str, *, expected_type: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RunnerProtocolError(
            f"Invalid JSON {expected_type}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise RunnerProtocolError(
            f"Runner {expected_type} must decode to a JSON object"
        )
    return data


def _require_string(
    data: dict[str, Any],
    field_name: str,
    *,
    owner: str = "request",
) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value:
        raise RunnerProtocolError(f"{owner}.{field_name} must be a non-empty string")
    return value


def _require_mapping(
    data: dict[str, Any],
    field_name: str,
    *,
    owner: str = "request",
) -> dict[str, Any]:
    value = data.get(field_name)
    if not isinstance(value, dict):
        raise RunnerProtocolError(f"{owner}.{field_name} must be a JSON object")
    return value


def _optional_mapping(
    data: dict[str, Any],
    field_name: str,
    *,
    owner: str,
) -> dict[str, Any]:
    value = data.get(field_name, {})
    if not isinstance(value, dict):
        raise RunnerProtocolError(f"{owner}.{field_name} must be a JSON object")
    return value
