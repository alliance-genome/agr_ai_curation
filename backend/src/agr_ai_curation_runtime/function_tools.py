"""Function tools whose failure messages to the model keep their established wording.

openai-agents 0.19.4+ removes argument-validation detail from tool errors while
``OPENAI_AGENTS_DONT_LOG_TOOL_DATA`` is on (the default, and our setting), and 0.23+
replaces every handled tool error with a fixed "An error occurred while running the
tool." Correction rounds (builder finalize, record_evidence, formatters, validators)
rely on the field-level detail, so every function tool built through this module
gets an explicit failure handler that returns the same text the model received
before the upgrade:

- a raised error: ``An error occurred while running the tool. Please try again.
  Error: <error>``
- invalid arguments: the same text with ``Invalid JSON input for tool <name>:
  <pydantic ValidationError>``, rebuilt by validating the call's own arguments with
  the parameter model the SDK builds for the tool (same name, same strict mode).

The SDK logging flag is left unchanged; this module logs nothing.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from agents import function_tool as _sdk_function_tool
from agents.exceptions import ModelBehaviorError
from agents.function_schema import function_schema
from agents.tool import FunctionTool
from pydantic import BaseModel, ValidationError

_DEFAULT = object()
_ERROR_PREFIX = "An error occurred while running the tool. Please try again. Error: "


def model_visible_tool_error(ctx: Any, error: Exception) -> str:
    """The message returned to the model for a failed tool call."""

    return f"{_ERROR_PREFIX}{error}"


def _argument_error_with_detail(
    ctx: Any, error: Exception, params_model: type[BaseModel]
) -> Exception:
    """Return the SDK's bare invalid-argument error with its validation detail restored.

    The SDK raises ``ModelBehaviorError("Invalid JSON input for tool <name>")`` for both
    malformed JSON and arguments its parameter model rejects. Malformed JSON keeps the
    bare message, as before the upgrade. Arguments that parse are validated again with
    the same model, and the resulting ``ValidationError`` is attached as the cause, so
    explicit handlers that read ``error.__cause__`` see what they saw before.
    """

    base_message = f"Invalid JSON input for tool {ctx.tool_name}"
    if not isinstance(error, ModelBehaviorError) or str(error) != base_message:
        return error
    try:
        arguments = json.loads(ctx.tool_arguments) if ctx.tool_arguments else {}
    except ValueError:
        return error
    if not isinstance(arguments, dict):
        return error
    try:
        params_model(**arguments)
    except ValidationError as validation_error:
        detailed = ModelBehaviorError(f"{base_message}: {validation_error}")
        detailed.__cause__ = validation_error
        return detailed
    return error


def _with_argument_detail(
    handler: Callable[[Any, Exception], Any], params_model: type[BaseModel]
) -> Callable[[Any, Exception], Any]:
    def failure_error_function(ctx: Any, error: Exception) -> Any:
        return handler(ctx, _argument_error_with_detail(ctx, error, params_model))

    return failure_error_function


def _propagate_with_argument_detail(
    params_model: type[BaseModel],
) -> Callable[[Any, Exception], None]:
    """Propagation policy (``failure_error_function=None``) with argument detail restored.

    An invalid-argument error is raised again with its validation detail, as the SDK
    raised it before the upgrade. Every other error returns ``None``, which makes the
    SDK re-raise the original exception exactly as it does for ``None``.
    """

    def failure_error_function(ctx: Any, error: Exception) -> None:
        detailed = _argument_error_with_detail(ctx, error, params_model)
        if detailed is not error:
            raise detailed
        return None

    return failure_error_function


def function_tool(
    func: Callable[..., Any] | None = None,
    *,
    failure_error_function: Any = _DEFAULT,
    **kwargs: Any,
) -> FunctionTool | Callable[[Callable[..., Any]], FunctionTool]:
    """``agents.function_tool`` with the established model-visible failure messages.

    Omitting ``failure_error_function`` uses ``model_visible_tool_error``. An explicit
    handler receives argument errors with their validation detail restored. ``None``
    still propagates every error; an invalid-argument error propagates with its
    validation detail. All other keywords pass to the SDK.
    """

    def create(the_func: Callable[..., Any]) -> FunctionTool:
        params_model = function_schema(
            func=the_func,
            docstring_style=kwargs.get("docstring_style"),
            name_override=kwargs.get("name_override"),
            use_docstring_info=kwargs.get("use_docstring_info", True),
            strict_json_schema=kwargs.get("strict_mode", True),
        ).params_pydantic_model
        if failure_error_function is None:
            handler = _propagate_with_argument_detail(params_model)
        else:
            handler = _with_argument_detail(
                model_visible_tool_error
                if failure_error_function is _DEFAULT
                else failure_error_function,
                params_model,
            )
        return _sdk_function_tool(the_func, failure_error_function=handler, **kwargs)

    if callable(func):
        return create(func)
    return create


__all__ = ["function_tool", "model_visible_tool_error"]
