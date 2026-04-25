"""AWS environment helpers shared by runtime integrations."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Dict, Iterator

AWS_PROFILE_ENV_VARS = ("AWS_PROFILE", "AWS_DEFAULT_PROFILE")


def pop_blank_aws_profile_env_vars() -> Dict[str, str]:
    """Remove blank AWS profile env vars and return values for restoration.

    Docker Compose can inject `AWS_PROFILE=` / `AWS_DEFAULT_PROFILE=` when the
    source shell leaves them unset. Botocore treats those blank strings as real
    profile names and raises `ProfileNotFound` before it can fall back to the
    EC2/ECS role or other credential providers.
    """
    cleared: Dict[str, str] = {}
    for key in AWS_PROFILE_ENV_VARS:
        value = os.getenv(key)
        if value is not None and not value.strip():
            cleared[key] = value
            os.environ.pop(key, None)
    return cleared


@contextmanager
def without_blank_aws_profile_env_vars() -> Iterator[None]:
    """Temporarily hide blank AWS profile env vars from botocore."""
    cleared = pop_blank_aws_profile_env_vars()
    try:
        yield
    finally:
        os.environ.update(cleared)
