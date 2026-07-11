#!/usr/bin/env python3
"""Render and validate the sole supported production Compose contract."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE = REPO_ROOT / "docker-compose.production.yml"
STATEFUL_SERVICES = {
    "clickhouse",
    "langfuse",
    "langfuse-worker",
    "loki",
    "minio",
    "postgres",
    "promtail",
    "redis",
    "weaviate",
}
INTERNAL_DATA_SERVICES = {
    "clickhouse",
    "loki",
    "minio",
    "postgres",
    "redis",
    "weaviate",
}
APP_SERVICES = {"backend", "frontend", "trace_review_backend"}
PINNED_APP_IMAGE_PATTERN = re.compile(
    r".+:(?:v\d+\.\d+\.\d+|sha-[0-9a-fA-F]{7,40})$"
)


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return None


def _environment(service: dict[str, Any]) -> dict[str, Any]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return environment
    result: dict[str, Any] = {}
    for entry in environment:
        key, _, value = str(entry).partition("=")
        result[key] = value
    return result


def _require_bool(
    errors: list[str], env: dict[str, Any], service: str, key: str, expected: bool
) -> None:
    actual = _as_bool(env.get(key))
    if actual is not expected:
        errors.append(f"{service}.{key} must render as {str(expected).lower()}")


def validate_config(
    config: dict[str, Any], *, enforce_operational_defaults: bool = False
) -> list[str]:
    """Return every unsafe effective value found in rendered Compose JSON."""
    errors: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return ["rendered Compose config has no services mapping"]

    missing = {"backend", "frontend", "trace_review_backend", "weaviate"} - services.keys()
    if missing:
        errors.append(f"required production services are missing: {', '.join(sorted(missing))}")
        return errors

    backend_env = _environment(services["backend"])
    frontend_env = _environment(services["frontend"])
    trace_env = _environment(services["trace_review_backend"])
    weaviate_env = _environment(services["weaviate"])

    _require_bool(errors, frontend_env, "frontend", "VITE_DEV_MODE", False)
    _require_bool(errors, backend_env, "backend", "DEV_MODE", False)
    _require_bool(errors, backend_env, "backend", "DEBUG", False)
    _require_bool(errors, backend_env, "backend", "SECURE_COOKIES", True)
    _require_bool(errors, backend_env, "backend", "HEALTH_CHECK_STRICT_MODE", True)
    _require_bool(
        errors,
        backend_env,
        "backend",
        "HEALTH_CHECK_REQUIRE_EXTERNAL_VALIDATION_DEPS",
        True,
    )
    _require_bool(
        errors,
        backend_env,
        "backend",
        "HEALTH_CHECK_REQUIRE_LITERATURE_DB",
        True,
    )
    auth_provider = str(backend_env.get("AUTH_PROVIDER", "")).strip().lower()
    required_auth_keys: tuple[str, ...] = ()
    if auth_provider == "oidc":
        required_auth_keys = ("OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_REDIRECT_URI")
    elif auth_provider == "cognito":
        required_auth_keys = (
            "COGNITO_USER_POOL_ID",
            "COGNITO_CLIENT_ID",
            "COGNITO_REDIRECT_URI",
        )
    else:
        errors.append("backend.AUTH_PROVIDER must be oidc or cognito in production")
    for key in required_auth_keys:
        if not str(backend_env.get(key, "")).strip():
            errors.append(f"backend.{key} is required for {auth_provider} production auth")

    _require_bool(errors, trace_env, "trace_review_backend", "DEV_MODE", False)
    _require_bool(errors, trace_env, "trace_review_backend", "SECURE_COOKIES", True)
    _require_bool(
        errors,
        weaviate_env,
        "weaviate",
        "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED",
        False,
    )
    _require_bool(
        errors, weaviate_env, "weaviate", "AUTHENTICATION_APIKEY_ENABLED", True
    )

    for key in ("AUTHENTICATION_APIKEY_ALLOWED_KEYS", "AUTHORIZATION_ADMINLIST_USERS"):
        value = str(weaviate_env.get(key, "")).strip()
        if not value or "change_me" in value.lower():
            errors.append(f"weaviate.{key} must be configured with a non-placeholder value")

    if enforce_operational_defaults:
        if str(backend_env.get("SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS")) != "2000":
            errors.append("backend.SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS must render as 2000")
        if str(backend_env.get("SENTRY_TRANSACTION_RETAINED_SPANS_MAX")) != "50":
            errors.append("backend.SENTRY_TRANSACTION_RETAINED_SPANS_MAX must render as 50")

    for service_name, service in services.items():
        image = str(service.get("image", ""))
        if not image:
            errors.append(f"{service_name}.image must be set")
        elif image.endswith(":latest") or ":latest@" in image:
            errors.append(f"{service_name}.image must not use the mutable latest tag")
        elif "change_me" in image.lower():
            errors.append(f"{service_name}.image must use a real pinned release tag")
        elif service_name in APP_SERVICES and not PINNED_APP_IMAGE_PATTERN.fullmatch(image):
            errors.append(
                f"{service_name}.image must use a vX.Y.Z or sha-<shortsha> tag"
            )
        if service_name in STATEFUL_SERVICES and "@sha256:" not in image:
            errors.append(f"{service_name}.image must be pinned by digest")

    for service_name in INTERNAL_DATA_SERVICES:
        if services.get(service_name, {}).get("ports"):
            errors.append(f"{service_name} must not publish data ports in production")

    return errors


def render_config(env_file: Path, compose_file: Path = PRODUCTION_COMPOSE) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose_file),
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"docker compose config failed: {detail}")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=PRODUCTION_COMPOSE)
    parser.add_argument(
        "--config-json",
        type=Path,
        help="Validate already-rendered JSON (test/diagnostic use only).",
    )
    args = parser.parse_args()

    try:
        config = (
            json.loads(args.config_json.read_text(encoding="utf-8"))
            if args.config_json
            else render_config(
                args.env_file.expanduser().resolve(),
                args.compose_file.expanduser().resolve(),
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"PRODUCTION PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 1

    # Keep operator-tunable Sentry limits overrideable at launch. The contract
    # suite opts into default enforcement separately so documented defaults
    # cannot drift without turning those operational knobs into fixed policy.
    errors = validate_config(config)
    if errors:
        print("PRODUCTION PREFLIGHT FAILED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("PRODUCTION PREFLIGHT PASSED: rendered Compose contract is fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
