"""Fixture tools used by package-runner unit tests."""


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
