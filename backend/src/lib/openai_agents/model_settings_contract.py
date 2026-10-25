"""Deterministic settings for executable contracts, never callback serialization.

SDK JSON deliberately omits retry callbacks. Bind a versioned declaration to the
actual application-created policy; resume still reconstructs authorized code and
compares contracts, never reconstructs executable code from this declaration.
"""
import json
from importlib.metadata import version
from weakref import WeakKeyDictionary


class ModelSettingsContractError(ValueError):
    """A runtime policy has no application-owned executable identity."""


_POLICY_CONTRACTS = WeakKeyDictionary()


def register_retry_policy(policy, descriptor):
    """Register factory-owned policies only; keep immutable JSON, not caller data."""
    serialized = json.dumps(descriptor, sort_keys=True, allow_nan=False)
    existing = _POLICY_CONTRACTS.get(policy)
    if existing is not None and existing != serialized:
        raise ModelSettingsContractError("Retry policy identity cannot be changed")
    _POLICY_CONTRACTS[policy] = serialized
    return policy


def model_settings_contract(settings):
    """Record live settings plus declared policy identity, without changing retries."""
    if settings is None:
        return None
    payload = settings.to_json_dict()
    retry = settings.retry
    if retry is not None:
        if retry.policy is None:
            descriptor = {"id": "openai-agents.default", "sdk_version": version("openai-agents")}
        else:
            try:
                serialized = _POLICY_CONTRACTS.get(retry.policy)
            except TypeError:
                serialized = None
            if serialized is None:
                raise ModelSettingsContractError("Retry policy has no registered executable identity")
            descriptor = json.loads(serialized)
        payload["retry"]["policy"] = descriptor
    return payload
