"""Resolve standalone authentication settings with the real Compose CLI."""

import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("provider", ["oidc", "cognito", "dev"])
@pytest.mark.parametrize("shell_override", [False, True])
def test_standalone_auth_effective_environment(tmp_path, provider, shell_override):
    # CI mounts the host's standalone Compose plugin; no Docker socket is needed.
    compose_bin = os.getenv("COMPOSE_BIN")
    if not compose_bin:
        pytest.skip("Compose CLI required; covered by TraceReview Backend Tests CI")
    source = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    if not source.exists():
        source = Path("/app/trace_review/docker-compose.yml")
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(source.read_text())
    values = {
        "AUTH_PROVIDER": provider,
        "DEV_MODE": "true" if provider == "dev" else "false",
        "OIDC_ISSUER_URL": "https://issuer.example.org",
        "OIDC_CLIENT_ID": "oidc-client",
        "OIDC_CLIENT_SECRET": "synthetic-oidc-secret",
        "OIDC_REDIRECT_URI": "https://review.example.org/api/auth/callback",
        "COGNITO_USER_POOL_ID": "us-east-1_synthetic",
        "COGNITO_CLIENT_ID": "cognito-client",
        "COGNITO_CLIENT_SECRET": "synthetic-cognito-secret",
        "COGNITO_REDIRECT_URI": "https://review.example.org/api/auth/callback",
        "SECURE_COOKIES": "true",
    }
    env_file = tmp_path / "deployment.env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    # Isolate from any real workstation credentials or Compose dotenv inputs.
    environment = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    if shell_override:
        environment["OIDC_CLIENT_ID"] = "shell-client"
        environment["SECURE_COOKIES"] = "false"
    command = [compose_bin, "--env-file", str(env_file), "-f", str(compose_file),
               "config", "--format", "json"]
    declared = subprocess.run(
        [*command, "--no-env-resolution"],
        env=environment, check=True, capture_output=True, text=True,
    )
    assert "env_file" not in json.loads(declared.stdout)["services"]["backend"]
    rendered = subprocess.run(command, env=environment, check=True,
                              capture_output=True, text=True)
    backend = json.loads(rendered.stdout)["services"]["backend"]
    assert "env_file" not in backend
    expected = values | ({"OIDC_CLIENT_ID": "shell-client", "SECURE_COOKIES": "false"}
                         if shell_override else {})
    for key, value in expected.items():
        assert backend["environment"][key] == value, key
