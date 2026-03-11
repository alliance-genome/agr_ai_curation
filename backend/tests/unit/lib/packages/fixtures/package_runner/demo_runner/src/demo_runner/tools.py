"""Fixture tools used by package-runner unit tests."""


class FakeSdkTool:
    """Minimal SDK-like tool object exposing on_invoke_tool only."""

    def __init__(self, *, prefix: str = "") -> None:
        self.prefix = prefix

    async def on_invoke_tool(self, _ctx, input_str: str):
        import json

        payload = json.loads(input_str or "{}")
        message = payload.get("message", "")
        punctuation = payload.get("punctuation", "!")
        return {"message": f"{self.prefix}{message}{punctuation}"}


def echo_value(value: str, prefix: str = "") -> dict[str, str]:
    return {"value": f"{prefix}{value}"}


def create_message_tool(context: dict[str, str]):
    document_id = context["document_id"]
    user_id = context["user_id"]

    def build_message(subject: str, punctuation: str = "!") -> dict[str, str]:
        return {
            "message": f"{subject} for {document_id} by {user_id}{punctuation}"
        }

    return build_message


def explode_value(value: str) -> dict[str, str]:
    raise RuntimeError(f"boom: {value}")


sdk_static_tool = FakeSdkTool(prefix="static:")


def create_sdk_context_tool(context: dict[str, str]):
    document_id = context["document_id"]
    user_id = context["user_id"]
    return FakeSdkTool(prefix=f"{document_id}:{user_id}:")
