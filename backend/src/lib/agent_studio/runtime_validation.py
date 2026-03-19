"""Runtime validation and diagnostics for unified agents table records."""

from __future__ import annotations

import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.lib.config.models_loader import list_models, load_models
from src.models.sql.agent import Agent as DBAgent
from src.models.sql.database import SessionLocal


_startup_report: Optional[Dict[str, Any]] = None
_REASONING_LEVEL_PATTERN = re.compile(r"^(minimal|low|medium|high)$")
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def get_agent_runtime_validation_strict_mode() -> bool:
    """Whether startup should fail fast for critical agent-config issues."""
    return _parse_bool_env("AGENT_RUNTIME_STRICT_MODE", False)


def _resolve_output_schema(schema_key: str) -> Optional[Any]:
    """Resolve output schema class by name from shared OpenAI agent models."""
    try:
        from src.lib.openai_agents import models as agent_models
    except Exception:
        return None
    return getattr(agent_models, schema_key, None)


def _load_runtime_policy() -> Dict[str, Any]:
    """Load catalog-backed runtime policy primitives lazily."""
    from src.lib.agent_studio import catalog_service

    return {
        "tool_bindings": catalog_service.TOOL_BINDINGS,
        "canonicalize_tool_id": catalog_service._canonicalize_tool_id,  # intentional internal reuse
        "document_tool_ids": set(catalog_service._DOCUMENT_TOOL_IDS),
        "agr_db_query_tool_ids": set(catalog_service._AGR_DB_QUERY_TOOL_IDS),
    }


def _fetch_active_agents() -> List[Any]:
    """Fetch active agent rows from unified agents table."""
    db = SessionLocal()
    try:
        return (
            db.query(DBAgent)
            .filter(DBAgent.is_active == True)  # noqa: E712
            .order_by(DBAgent.agent_key.asc())
            .all()
        )
    finally:
        db.close()


def _normalize_tool_ids(raw_tool_ids: Any) -> Tuple[List[str], Optional[str]]:
    """Normalize DB tool_ids into a cleaned list; return error for invalid shapes."""
    if raw_tool_ids in (None, ""):
        return [], None
    if not isinstance(raw_tool_ids, list):
        return [], f"tool_ids must be a list, got {type(raw_tool_ids).__name__}"

    normalized: List[str] = []
    for item in raw_tool_ids:
        tool_id = str(item or "").strip()
        if not tool_id:
            continue
        if tool_id not in normalized:
            normalized.append(tool_id)
    return normalized, None


def _load_expected_system_agent_keys() -> Tuple[set[str], Optional[str]]:
    """Load expected system-agent keys from layered runtime agent definitions."""
    try:
        from src.lib.config.agent_loader import load_agent_definitions

        agent_defs = load_agent_definitions()
        expected_keys = set()
        for agent in agent_defs.values():
            # Canonicalize PDF agent to `pdf_extraction` while preserving
            # legacy folder-key behavior for other agents.
            if agent.folder_name == "pdf":
                expected_keys.add(agent.agent_id)
            else:
                expected_keys.add(agent.folder_name)
        return expected_keys, None
    except Exception as exc:
        return set(), f"Failed to load expected system agents from layered sources: {exc}"


def _allow_unseeded_core_only_runtime(
    *,
    expected_system_agent_keys: set[str],
    actual_system_agent_keys: set[str],
    agent_count: int,
) -> bool:
    """Whether a fresh `agr.core`-only runtime should skip missing-system hard failure."""
    return (
        agent_count == 0
        and not actual_system_agent_keys
        and expected_system_agent_keys in (
            {"supervisor"},
            {"supervisor", "chat_output"},
        )
    )


def _disable_agents_with_missing_tools(report: Dict[str, Any]) -> None:
    """Best-effort deactivate agents that reference unavailable tools."""
    disable_reasons = {
        str(agent.get("agent_key") or "").strip(): str(agent.get("disable_reason") or "").strip()
        for agent in report.get("agents", [])
        if agent.get("disabled") and str(agent.get("agent_key") or "").strip()
    }
    if not disable_reasons:
        return

    db = SessionLocal()
    try:
        rows = (
            db.query(DBAgent)
            .filter(DBAgent.agent_key.in_(sorted(disable_reasons)))
            .all()
        )
        for row in rows:
            row.is_active = False
            row.supervisor_enabled = False
            logger.warning(
                "Agent '%s' disabled: %s",
                row.agent_key,
                disable_reasons.get(str(row.agent_key), "missing runtime tool dependencies"),
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to deactivate agent rows with missing runtime tool dependencies"
        )
    finally:
        db.close()


def build_agent_runtime_report(
    *,
    strict_mode: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build diagnostics report for runtime agent configuration safety."""
    strict = (
        get_agent_runtime_validation_strict_mode()
        if strict_mode is None
        else bool(strict_mode)
    )
    errors: List[str] = []
    warnings: List[str] = []
    agent_payload: List[Dict[str, Any]] = []

    try:
        load_models()
        known_model_ids = {model.model_id for model in list_models()}
    except Exception as exc:
        return {
            "status": "unhealthy",
            "strict_mode": strict,
            "validated_at": _now_iso(),
            "errors": [f"Failed to load models.yaml: {exc}"],
            "warnings": [],
            "agents": [],
            "summary": {
                "agent_count": 0,
                "unhealthy_agent_count": 0,
                "degraded_agent_count": 0,
                "missing_tool_backfill_candidates": 0,
                "critical_missing_tool_backfill_candidates": 0,
                "missing_system_agent_count": 0,
            },
        }

    try:
        policy = _load_runtime_policy()
    except Exception as exc:
        return {
            "status": "unhealthy",
            "strict_mode": strict,
            "validated_at": _now_iso(),
            "errors": [f"Failed to load runtime tool policy: {exc}"],
            "warnings": [],
            "agents": [],
            "summary": {
                "agent_count": 0,
                "unhealthy_agent_count": 0,
                "degraded_agent_count": 0,
                "missing_tool_backfill_candidates": 0,
                "critical_missing_tool_backfill_candidates": 0,
                "missing_system_agent_count": 0,
            },
        }

    tool_bindings = dict(policy["tool_bindings"])
    canonicalize_tool_id = policy["canonicalize_tool_id"]
    document_tool_ids = set(policy["document_tool_ids"])
    agr_db_query_tool_ids = set(policy["agr_db_query_tool_ids"])
    critical_tool_ids = document_tool_ids | agr_db_query_tool_ids

    agents = _fetch_active_agents()
    system_rows_by_key: Dict[str, Dict[str, Any]] = {}

    # First pass: gather canonicalized system template tool profiles.
    for row in agents:
        raw_tool_ids, tool_shape_error = _normalize_tool_ids(getattr(row, "tool_ids", []))
        canonical_tool_ids: List[str] = []
        if tool_shape_error is None:
            for tool_id in raw_tool_ids:
                canonical_id = str(canonicalize_tool_id(tool_id)).strip()
                if canonical_id and canonical_id not in canonical_tool_ids:
                    canonical_tool_ids.append(canonical_id)

        if getattr(row, "visibility", None) == "system":
            system_rows_by_key[str(row.agent_key)] = {
                "tool_ids": canonical_tool_ids,
                "has_critical_tools": bool(set(canonical_tool_ids) & critical_tool_ids),
            }

    missing_system_agent_count = 0
    expected_system_agent_keys, expected_system_agent_error = _load_expected_system_agent_keys()
    if expected_system_agent_error:
        warnings.append(expected_system_agent_error)
    else:
        actual_system_agent_keys = set(system_rows_by_key.keys())
        missing_system_agents = sorted(expected_system_agent_keys - actual_system_agent_keys)
        if missing_system_agents:
            missing_system_agent_count = len(missing_system_agents)
            if _allow_unseeded_core_only_runtime(
                expected_system_agent_keys=expected_system_agent_keys,
                actual_system_agent_keys=actual_system_agent_keys,
                agent_count=len(agents),
            ):
                warnings.append(
                    "No active system agents are seeded yet; allowing core-only runtime "
                    "bootstrap with expected agent(s): "
                    + ", ".join(missing_system_agents)
                )
            else:
                errors.append(
                    "Missing active system agents in unified agents table: "
                    + ", ".join(missing_system_agents)
                )

    unhealthy_agent_count = 0
    degraded_agent_count = 0
    disabled_agent_count = 0
    missing_tool_candidates = 0
    critical_missing_tool_candidates = 0

    for row in agents:
        row_errors: List[str] = []
        row_warnings: List[str] = []
        disable_reason: Optional[str] = None
        raw_tool_ids, tool_shape_error = _normalize_tool_ids(getattr(row, "tool_ids", []))
        canonical_tool_ids: List[str] = []

        if tool_shape_error:
            row_errors.append(tool_shape_error)
        else:
            unknown_tool_ids: List[str] = []
            for tool_id in raw_tool_ids:
                canonical_id = str(canonicalize_tool_id(tool_id)).strip()
                if canonical_id and canonical_id not in canonical_tool_ids:
                    canonical_tool_ids.append(canonical_id)
                if canonical_id and canonical_id not in tool_bindings:
                    unknown_tool_ids.append(tool_id)
            if unknown_tool_ids:
                row_warnings.append(
                    "Unknown tool_ids: " + ", ".join(sorted(set(unknown_tool_ids)))
                )
                disable_reason = (
                    "references tools from uninstalled package(s). Install the "
                    "required package and restart to enable."
                )
                row_warnings.append(
                    "Disabled: " + disable_reason
                )

        model_id = str(getattr(row, "model_id", "") or "").strip()
        if not model_id:
            row_errors.append("model_id is required")
        elif model_id not in known_model_ids:
            row_errors.append(f"Unknown model_id '{model_id}'")

        reasoning = getattr(row, "model_reasoning", None)
        if isinstance(reasoning, str) and reasoning.strip():
            if not _REASONING_LEVEL_PATTERN.match(reasoning.strip()):
                row_warnings.append(f"Invalid model_reasoning '{reasoning}'")

        output_schema_key = str(getattr(row, "output_schema_key", "") or "").strip()
        if output_schema_key and _resolve_output_schema(output_schema_key) is None:
            row_errors.append(f"Unknown output_schema_key '{output_schema_key}'")

        visibility = str(getattr(row, "visibility", "") or "").strip()
        user_id = getattr(row, "user_id", None)
        project_id = getattr(row, "project_id", None)

        if visibility == "project" and project_id is None:
            row_errors.append("project visibility requires project_id")
        if visibility in {"private", "project"} and user_id is None:
            row_errors.append(f"{visibility} visibility requires user_id")
        if visibility == "system" and user_id is not None:
            row_warnings.append("system visibility should typically have null user_id")

        template_source = str(getattr(row, "template_source", "") or "").strip()
        missing_tool_candidate = False
        critical_missing_tool_candidate = False
        suggested_tool_ids: List[str] = []

        if template_source and visibility in {"private", "project"}:
            parent_profile = system_rows_by_key.get(template_source)
            if parent_profile is None:
                row_warnings.append(
                    f"template_source '{template_source}' does not match an active system template"
                )
            else:
                parent_tool_ids = list(parent_profile.get("tool_ids", []))
                suggested_tool_ids = parent_tool_ids
                parent_has_critical_tools = bool(parent_profile.get("has_critical_tools"))
                if not canonical_tool_ids and parent_tool_ids:
                    missing_tool_candidate = True
                    missing_tool_candidates += 1
                    if parent_has_critical_tools:
                        critical_missing_tool_candidate = True
                        critical_missing_tool_candidates += 1
                        message = (
                            f"Likely missing critical tools from template '{template_source}'. "
                            f"Suggested restore: {', '.join(parent_tool_ids)}"
                        )
                        if strict:
                            row_errors.append(message)
                        else:
                            row_warnings.append(message)
                    else:
                        row_warnings.append(
                            f"Likely missing tools from template '{template_source}'. "
                            f"Suggested restore: {', '.join(parent_tool_ids)}"
                        )

        if row_errors:
            unhealthy_agent_count += 1
        elif row_warnings:
            degraded_agent_count += 1
        if disable_reason:
            disabled_agent_count += 1

        for msg in row_errors:
            errors.append(f"{row.agent_key}: {msg}")
        for msg in row_warnings:
            warnings.append(f"{row.agent_key}: {msg}")

        agent_payload.append(
            {
                "agent_key": str(row.agent_key),
                "name": str(getattr(row, "name", "") or ""),
                "visibility": visibility,
                "template_source": template_source or None,
                "model_id": model_id,
                "tool_ids": canonical_tool_ids,
                "errors": row_errors,
                "warnings": row_warnings,
                "disabled": bool(disable_reason),
                "disable_reason": disable_reason,
                "missing_tool_backfill_candidate": missing_tool_candidate,
                "critical_missing_tool_backfill_candidate": critical_missing_tool_candidate,
                "suggested_tool_ids": suggested_tool_ids,
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
        "agents": agent_payload,
        "summary": {
            "agent_count": len(agent_payload),
            "unhealthy_agent_count": unhealthy_agent_count,
            "degraded_agent_count": degraded_agent_count,
            "disabled_agent_count": disabled_agent_count,
            "missing_tool_backfill_candidates": missing_tool_candidates,
            "critical_missing_tool_backfill_candidates": critical_missing_tool_candidates,
            "missing_system_agent_count": missing_system_agent_count,
        },
    }


def validate_agent_runtime_contracts(
    *,
    strict_mode: Optional[bool] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Validate runtime agent contracts and return `(is_valid, report)`."""
    report = build_agent_runtime_report(strict_mode=strict_mode)
    return (len(report.get("errors", [])) == 0, report)


def validate_and_cache_agent_runtime_contracts(
    *,
    strict_mode: Optional[bool] = None,
) -> Dict[str, Any]:
    """Validate agent runtime contracts and cache startup report."""
    global _startup_report

    is_valid, report = validate_agent_runtime_contracts(strict_mode=strict_mode)
    _disable_agents_with_missing_tools(report)
    _startup_report = deepcopy(report)
    if not is_valid:
        joined = "; ".join(report.get("errors", []))
        raise RuntimeError(f"Agent runtime validation failed: {joined}")
    return report


def get_startup_agent_validation_report() -> Optional[Dict[str, Any]]:
    """Return cached startup agent validation report, if available."""
    if _startup_report is None:
        return None
    return deepcopy(_startup_report)


def reset_startup_agent_validation_report() -> None:
    """Clear cached startup agent validation report (tests)."""
    global _startup_report
    _startup_report = None
