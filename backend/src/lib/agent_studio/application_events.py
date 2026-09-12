"""Server-owned instructions for UI actions, never synthetic curator speech."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

APPLICATION_EVENT_MESSAGE_TYPE = "agent_studio_application_event"


class ApplicationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["draft_applied"]
    event_id: UUID
    output_mode_node_ids: list[str] = Field(default_factory=list)


def application_event_instruction(event: ApplicationEvent) -> str:
    """Only fixed application text is promoted into model instructions."""
    text = (
        "Application event (not a curator message): changes were applied to the draft. "
        "Continue with the next agreed step; if the request is complete, briefly confirm it."
    )
    if event.output_mode_node_ids:
        text += (
            " Newly added file-output nodes still need an explicit output-mode choice. "
            "Before declaring setup complete, explain fast/direct unchanged structured export "
            "versus AI-formatted output and ask the curator which they prefer. Recommend direct "
            "when copying saved values unchanged; explain that formatter prompts will not run. "
            "Do not treat default AI mode as a choice, or enable direct without consent and a "
            "valid selected_fields plan. Ask only about the newly added applicable outputs."
        )
    return text
