#!/usr/bin/env python3
"""Select image work from the GitHub event's complete change set."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


NGINX_INPUTS = {
    "frontend/Dockerfile",
    "frontend/Dockerfile.dockerignore",
    "frontend/.dockerignore",
    "frontend/nginx.conf",
    "scripts/testing/frontend_nginx_pdf_limit_contract.sh",
    "scripts/testing/ci_image_scope.py",
    "scripts/tests/test_ci_image_scope.py",
    ".github/workflows/test.yml",
}


def select_images(paths: list[str]) -> dict[str, bool]:
    return {
        "nginx_runtime": any(
            path in NGINX_INPUTS or path.startswith("frontend/docker-entrypoint.d/")
            for path in paths
        ),
        "backend_smoke": any(path.startswith("backend/") for path in paths),
    }


def changed_paths(event: dict, event_name: str) -> list[str]:
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        head = event["pull_request"]["head"]["sha"]
        separator = "..."
    elif event_name == "push":
        base, head = event["before"], event["after"]
        separator = ".."
    else:
        raise ValueError("event has no supported change range")
    for revision in (base, head):
        if (
            not isinstance(revision, str)
            or not re.fullmatch(r"[0-9a-fA-F]{40}", revision)
            or set(revision) == {"0"}
        ):
            raise ValueError("event has no usable commit range")
    result = subprocess.run(
        [
            "git",
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            f"{base}{separator}{head}",
            "--",
        ],
        check=True,
        capture_output=True,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main() -> None:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        selected = select_images(changed_paths(event, os.environ["GITHUB_EVENT_NAME"]))
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        # Missing history/new branches must never silently suppress validation.
        print(
            f"Image scope unavailable ({type(error).__name__}); run all image checks.",
            file=sys.stderr,
        )
        selected = {"nginx_runtime": True, "backend_smoke": True}
    for name, enabled in selected.items():
        print(f"{name}={str(enabled).lower()}")


if __name__ == "__main__":
    main()
