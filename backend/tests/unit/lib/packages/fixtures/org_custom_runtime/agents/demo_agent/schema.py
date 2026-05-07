from pydantic import BaseModel


class DemoValidationEnvelope(BaseModel):
    """Neutral demo validation result envelope."""

    demo_id: str
    status: str
