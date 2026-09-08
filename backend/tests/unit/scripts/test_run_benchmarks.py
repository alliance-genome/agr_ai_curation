"""Entrypoint regression: CLI import must never initialize execution."""

from pathlib import Path
import subprocess
import sys


def test_entrypoint_help_is_api_only():
    root = Path(__file__).resolve().parents[4]
    result = subprocess.run([sys.executable, str(root / "scripts/run_benchmarks.py"), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "watch" in result.stdout
    assert "submit" in result.stdout
    assert "--profile" not in result.stdout


def test_entrypoint_import_does_not_load_execution_packages():
    root = Path(__file__).resolve().parents[4]
    result = subprocess.run([
        sys.executable, "-c",
        "import runpy,sys; runpy.run_path('scripts/run_benchmarks.py'); "
        "assert not any(n == 'src.lib.benchmarks' or n.startswith('src.lib.benchmarks.') for n in sys.modules); "
        "assert 'agents' not in sys.modules; print('API_ONLY_IMPORT_OK')",
    ], cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == "API_ONLY_IMPORT_OK"
