"""Regression guard: package __init__.py files must not pull in heavy runtime deps.

Importing the ``agent_studio`` or ``curation_workspace`` *package* should
never eagerly load the OpenAI Agents SDK (``agents``) or trigger side-effects
such as flow-tool auto-registration.  Lightweight consumers (models,
validation, extraction results) must remain importable without those costs.

Each test spawns a **subprocess** so that ``sys.modules`` starts clean.
"""

import subprocess
import sys
import textwrap

import pytest

_BACKEND_DIR = str(__import__("pathlib").Path(__file__).resolve().parents[3])

# Modules that must NOT appear in sys.modules after a lightweight package import.
_HEAVY_MODULES = ("agents", "agents.run", "agents.agent")


def _run_import_check(import_statement: str, forbidden: tuple[str, ...] = _HEAVY_MODULES) -> None:
    """Run *import_statement* in a subprocess and assert *forbidden* modules are absent."""
    script = textwrap.dedent(f"""\
        import sys, os, json
        # Ensure backend is on the path
        sys.path.insert(0, {_BACKEND_DIR!r})
        os.chdir({_BACKEND_DIR!r})

        {import_statement}

        # Report which forbidden modules were loaded
        loaded = [m for m in {forbidden!r} if m in sys.modules]
        print(json.dumps(loaded))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(
            f"Subprocess failed (rc={result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    import json

    loaded = json.loads(result.stdout.strip())
    if loaded:
        pytest.fail(
            f"Importing the package eagerly loaded heavy modules: {loaded}\n"
            f"Statement: {import_statement}"
        )


# ── agent_studio ──────────────────────────────────────────────────────────

class TestAgentStudioInitNoHeavyDeps:
    """agent_studio package must be importable without loading agents SDK."""

    def test_package_import_does_not_load_agents_sdk(self):
        _run_import_check("import src.lib.agent_studio")

    def test_models_import_does_not_load_agents_sdk(self):
        _run_import_check("from src.lib.agent_studio.models import ChatMessage")

    def test_trace_context_import_does_not_load_agents_sdk(self):
        _run_import_check(
            "from src.lib.agent_studio.trace_context_service import TraceContextError"
        )

    def test_no_auto_registration_side_effect(self):
        """Importing the package must not call register_flow_tools()."""
        script = textwrap.dedent(f"""\
            import sys, os
            sys.path.insert(0, {_BACKEND_DIR!r})
            os.chdir({_BACKEND_DIR!r})

            import src.lib.agent_studio

            # flow_tools should not have been imported at all
            if "src.lib.agent_studio.flow_tools" in sys.modules:
                print("FAIL:flow_tools_imported")
            else:
                print("OK")
        """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            pytest.fail(
                f"Subprocess failed (rc={result.returncode}).\n"
                f"stderr: {result.stderr}"
            )
        assert result.stdout.strip() == "OK", (
            "flow_tools was imported as a side-effect of importing agent_studio"
        )


# ── curation_workspace ────────────────────────────────────────────────────

class TestCurationWorkspaceInitNoHeavyDeps:
    """curation_workspace package must be importable without loading agents SDK."""

    def test_package_import_does_not_load_agents_sdk(self):
        _run_import_check("import src.lib.curation_workspace")

    def test_models_import_does_not_load_agents_sdk(self):
        _run_import_check(
            "from src.lib.curation_workspace.models import CurationCandidate"
        )

    def test_extraction_results_import_does_not_load_agents_sdk(self):
        _run_import_check(
            "from src.lib.curation_workspace.extraction_results import "
            "persist_extraction_result"
        )

    def test_pipeline_import_does_not_load_agents_sdk(self):
        _run_import_check(
            "from src.lib.curation_workspace.pipeline import "
            "execute_post_curation_pipeline"
        )
