"""Stable benchmark source identity, shared by explicit migration and capture."""

import json
from uuid import NAMESPACE_URL, UUID, uuid5


def benchmark_attempt_id(*, invocation_id: UUID, model_request_id: UUID | None,
                         deployment_id: str, source_namespace: str) -> UUID:
    """An unmeasured source surrogate never claims to be a measured request ID."""
    return model_request_id if model_request_id is not None else uuid5(NAMESPACE_URL, json.dumps(
        ["agr-ai-curation:benchmark-cost-source:v1", deployment_id,
         source_namespace, str(invocation_id)], separators=(",", ":"), ensure_ascii=True,
    ))
