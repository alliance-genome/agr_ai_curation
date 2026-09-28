"""Exercise image selection against real multi-commit GitHub event ranges."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "testing" / "ci_image_scope.py"
spec = importlib.util.spec_from_file_location("ci_image_scope", SCRIPT)
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class ImageScopeTests(unittest.TestCase):
    def test_runtime_inputs_select_nginx(self):
        for path in scope.NGINX_INPUTS | {"frontend/docker-entrypoint.d/new.sh"}:
            with self.subTest(path=path):
                self.assertTrue(scope.select_images([path])["nginx_runtime"])

    def test_ui_only_changes_skip_both_images(self):
        self.assertEqual(
            scope.select_images(["frontend/src/Profile.tsx", "docs/README.md"]),
            {"nginx_runtime": False, "backend_smoke": False},
        )

    def test_backend_changes_select_smoke_only(self):
        self.assertEqual(
            scope.select_images(["backend/src/main.py"]),
            {"nginx_runtime": False, "backend_smoke": True},
        )


class EventRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.base = self.commit("README.md", "base")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def commit(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.git("add", "-A")
        self.git("commit", "-qm", content)
        return self.git("rev-parse", "HEAD")

    def select(self, event, name="pull_request"):
        event_path = self.root / "event.json"
        event_path.write_text(json.dumps(event))
        result = subprocess.run(
            ["python3", str(SCRIPT)],
            cwd=self.root,
            env={
                **os.environ,
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_EVENT_NAME": name,
            },
            text=True,
            capture_output=True,
            check=True,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def pr(self, head, base=None):
        return {
            "pull_request": {"base": {"sha": base or self.base}, "head": {"sha": head}}
        }

    def test_pr_includes_earlier_commits(self):
        self.commit("frontend/nginx.conf", "runtime")
        head = self.commit("frontend/src/ui.tsx", "ui")
        self.assertEqual(self.select(self.pr(head))["nginx_runtime"], "true")

    def test_push_uses_complete_before_after_range(self):
        self.commit("backend/src/main.py", "backend")
        head = self.commit("README.md", "docs")
        self.assertEqual(
            self.select({"before": self.base, "after": head}, "push")["backend_smoke"],
            "true",
        )

    def test_pr_excludes_base_branch_only_changes(self):
        head = self.commit("frontend/src/ui.tsx", "ui")
        self.git("checkout", "-q", "-b", "base-branch", self.base)
        new_base = self.commit("frontend/nginx.conf", "base-only")
        self.assertEqual(self.select(self.pr(head, new_base))["nginx_runtime"], "false")

    def test_rename_away_from_runtime_input_is_included(self):
        base = self.commit("frontend/nginx.conf", "runtime")
        self.git("mv", "frontend/nginx.conf", "frontend/old.conf")
        head = self.commit("README.md", "rename")
        self.assertEqual(self.select(self.pr(head, base))["nginx_runtime"], "true")

    def test_missing_history_runs_all(self):
        self.assertEqual(
            self.select(self.pr("f" * 40)),
            {"nginx_runtime": "true", "backend_smoke": "true"},
        )

    def test_new_branch_runs_all(self):
        self.assertEqual(
            self.select({"before": "0" * 40, "after": self.base}, "push"),
            {"nginx_runtime": "true", "backend_smoke": "true"},
        )

    def test_unsupported_event_runs_all(self):
        self.assertEqual(
            self.select({}, "workflow_dispatch"),
            {"nginx_runtime": "true", "backend_smoke": "true"},
        )


if __name__ == "__main__":
    unittest.main()
