#!/usr/bin/env python3
"""Docker-backed package freshness proof; uses system Docker unless env overrides it.

Run: python3 scripts/tests/test_docker_test_compose_freshness.py
Builds a small installed-package fixture, with no services or external providers.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def compose(workspace, *args, capture=False):
    return subprocess.run(
        ["bash", str(workspace / "scripts/testing/docker-test-compose.sh"),
         "--rootful", *args],
        check=True, text=True, stdout=subprocess.PIPE if capture else None,
        env={key: value for key, value in os.environ.items()
             if key not in {"COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "RERANK_PROVIDER"}},
    ).stdout


def main():
    with tempfile.TemporaryDirectory(prefix="compose-freshness-") as directory:
        workspaces = [Path(directory) / name / "checkout" for name in ("a", "b")]
        try:
            for workspace in workspaces:
                for relative in ("scripts/testing/docker-test-compose.sh",
                                 "scripts/lib/rerank_provider_common.sh"):
                    destination = workspace / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / relative, destination)
                shutil.copytree(ROOT / "backend/auth_runtime", workspace / "auth_runtime",
                                ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build"))
                (workspace / "Dockerfile").write_text(
                    "FROM python:3.11-slim\n"
                    "COPY auth_runtime /tmp/auth_runtime\n"
                    "RUN pip install --no-cache-dir --no-deps /tmp/auth_runtime\n"
                )
                (workspace / "docker-compose.test.yml").write_text(
                    "services:\n  backend-unit-tests:\n    build: .\n    network_mode: none\n"
                )

            def check(workspace, expected):
                output = compose(workspace, "run", "--rm", "--no-deps", "backend-unit-tests",
                                 "python", "-c",
                                 "import auth_runtime.freshness_probe as p; "
                                 "assert '/site-packages/' in p.__file__; "
                                 f"assert p.VALUE == {expected!r}, p.VALUE; print(p.VALUE)",
                                 capture=True)
                assert output.strip().endswith(expected), output

            def marker(workspace, value):
                (workspace / "auth_runtime/auth_runtime/freshness_probe.py").write_text(
                    f"VALUE = {value!r}\n")

            a, b = workspaces
            marker(a, "initial-a")
            marker(b, "distinct-b")
            check(a, "initial-a")
            marker(a, "updated-a")
            check(a, "updated-a")
            check(b, "distinct-b")
            check(a, "updated-a")
            configs = [json.loads(compose(w, "config", "--format", "json", capture=True))
                       for w in workspaces]
            assert configs[0]["name"] != configs[1]["name"]
            print("Installed package freshness and workspace isolation passed")
        finally:
            for workspace in workspaces:
                if (workspace / "docker-compose.test.yml").exists():
                    compose(workspace, "down", "--volumes", "--remove-orphans", "--rmi", "local")


if __name__ == "__main__":
    main()
