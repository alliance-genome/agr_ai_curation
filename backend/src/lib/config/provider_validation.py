"""LLM provider/model contract validation and diagnostics reporting."""

from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .models_loader import list_models, load_models
from .providers_loader import list_providers, load_providers


_startup_report: Optional[Dict[str, Any]] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def get_provider_validation_strict_mode() -> bool:
    """Whether startup should fail fast for provider env-readiness issues."""
    return _parse_bool_env("LLM_PROVIDER_STRICT_MODE", True)


def build_provider_runtime_report(
    *,
    strict_mode: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build a full provider/model diagnostics report."""
    strict = get_provider_validation_strict_mode() if strict_mode is None else bool(strict_mode)
    errors: List[str] = []
    warnings: List[str] = []
    providers_payload: List[Dict[str, Any]] = []
    models_payload: List[Dict[str, Any]] = []

    try:
        load_providers()
        providers = list_providers()
    except Exception as exc:
        return {
            "status": "unhealthy",
            "strict_mode": strict,
            "validated_at": _now_iso(),
            "errors": [f"Failed to load providers.yaml: {exc}"],
            "warnings": [],
            "providers": [],
            "models": [],
            "summary": {
                "provider_count": 0,
                "model_count": 0,
                "ready_provider_count": 0,
                "missing_key_provider_count": 0,
                "mapped_model_count": 0,
            },
        }

    try:
        load_models()
        models = list_models()
    except Exception as exc:
        return {
            "status": "unhealthy",
            "strict_mode": strict,
            "validated_at": _now_iso(),
            "errors": [f"Failed to load models.yaml: {exc}"],
            "warnings": [],
            "providers": [],
            "models": [],
            "summary": {
                "provider_count": len(providers),
                "model_count": 0,
                "ready_provider_count": 0,
                "missing_key_provider_count": 0,
                "mapped_model_count": 0,
            },
        }

    providers_by_id = {p.provider_id: p for p in providers}
    model_ids_by_provider: Dict[str, List[str]] = {p.provider_id: [] for p in providers}
    visible_model_ids_by_provider: Dict[str, List[str]] = {p.provider_id: [] for p in providers}

    for model in models:
        provider_id = str(model.provider or "").strip().lower()
        provider_exists = provider_id in providers_by_id
        models_payload.append(
            {
                "model_id": model.model_id,
                "provider_id": provider_id,
                "provider_exists": provider_exists,
                "curator_visible": bool(getattr(model, "curator_visible", True)),
            }
        )
        if provider_exists:
            model_ids_by_provider[provider_id].append(model.model_id)
            if bool(getattr(model, "curator_visible", True)):
                visible_model_ids_by_provider[provider_id].append(model.model_id)
        else:
            errors.append(
                f"Model '{model.model_id}' references unknown provider '{provider_id}'"
            )

    missing_key_provider_count = 0
    ready_provider_count = 0
    for provider in providers:
        mapped_models = sorted(model_ids_by_provider.get(provider.provider_id, []))
        mapped_visible_models = sorted(visible_model_ids_by_provider.get(provider.provider_id, []))
        used_by_models = bool(mapped_models)
        api_key_present = bool(os.getenv(provider.api_key_env))
        required_for_runtime = bool(provider.default_for_runner or used_by_models)
        base_url_present = bool(os.getenv(provider.base_url_env)) if provider.base_url_env else False
        base_url_configured = bool(base_url_present or provider.default_base_url)

        readiness = "ready"
        if required_for_runtime and not api_key_present:
            readiness = "missing_api_key"
            missing_key_provider_count += 1
            message = (
                f"Provider '{provider.provider_id}' is required by runtime "
                f"but env var '{provider.api_key_env}' is not set"
            )
            if strict:
                errors.append(message)
            else:
                warnings.append(message)
        elif not required_for_runtime:
            readiness = "unused"

        if readiness == "ready":
            ready_provider_count += 1

        if provider.default_for_runner and not mapped_models:
            warnings.append(
                f"Default runner provider '{provider.provider_id}' has no mapped models in models.yaml"
            )

        providers_payload.append(
            {
                "provider_id": provider.provider_id,
                "driver": provider.driver,
                "api_mode": provider.api_mode,
                "api_key_env": provider.api_key_env,
                "api_key_present": api_key_present,
                "base_url_env": provider.base_url_env,
                "base_url_configured": base_url_configured,
                "default_for_runner": bool(provider.default_for_runner),
                "mapped_model_ids": mapped_models,
                "mapped_curator_visible_model_ids": mapped_visible_models,
                "supports_parallel_tool_calls": bool(provider.supports_parallel_tool_calls),
                "readiness": readiness,
            }
        )

    status = "healthy"
    if errors:
        status = "unhealthy"
    elif warnings:
        status = "degraded"

    return {
        "status": status,
        "strict_mode": strict,
        "validated_at": _now_iso(),
        "errors": errors,
        "warnings": warnings,
        "providers": sorted(providers_payload, key=lambda p: p["provider_id"]),
        "models": sorted(models_payload, key=lambda m: m["model_id"]),
        "summary": {
            "provider_count": len(providers_payload),
            "model_count": len(models_payload),
            "ready_provider_count": ready_provider_count,
            "missing_key_provider_count": missing_key_provider_count,
            "mapped_model_count": sum(len(v) for v in model_ids_by_provider.values()),
        },
    }


def validate_provider_runtime_contracts(
    *,
    strict_mode: Optional[bool] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Validate contracts and return `(is_valid, report)`."""
    report = build_provider_runtime_report(strict_mode=strict_mode)
    return (len(report.get("errors", [])) == 0, report)


def validate_and_cache_provider_runtime_contracts(
    *,
    strict_mode: Optional[bool] = None,
) -> Dict[str, Any]:
    """Validate provider runtime contracts and cache startup report."""
    global _startup_report

    is_valid, report = validate_provider_runtime_contracts(strict_mode=strict_mode)
    _startup_report = deepcopy(report)
    if not is_valid:
        joined = "; ".join(report.get("errors", []))
        raise RuntimeError(f"LLM provider validation failed: {joined}")
    return report


def get_startup_provider_validation_report() -> Optional[Dict[str, Any]]:
    """Return cached startup validation report, if available."""
    if _startup_report is None:
        return None
    return deepcopy(_startup_report)


def reset_startup_provider_validation_report() -> None:
    """Clear cached startup report (tests)."""
    global _startup_report
    _startup_report = None
