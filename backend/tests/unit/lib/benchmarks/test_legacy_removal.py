"""Regression guard for the forward-only benchmark v1 removal."""

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
BACKEND_SOURCE = REPOSITORY_ROOT / "backend" / "src"

REMOVED_FILES = (
    "backend/src/api/admin/benchmarks.py",
    "backend/src/lib/benchmarks/adjudication.py",
    "backend/src/lib/benchmarks/loader.py",
    "backend/src/lib/benchmarks/scoring.py",
    "backend/src/lib/benchmarks/service.py",
)
REMOVED_SETTINGS = (
    "BENCHMARK_ADJUDICATION_",
    "BENCHMARK_ARTIFACT_",
    "BENCHMARK_CASE_LIMIT",
    "BENCHMARK_INLINE_MAX_BYTES",
    "BENCHMARK_MATRIX_LIMIT",
    "BENCHMARK_MAX_CONCURRENCY",
    "BENCHMARK_PREVIEW_MAX_CHARS",
    "BENCHMARK_RESULT_LIMIT",
    "BENCHMARK_RETRIES",
    "BENCHMARK_TIMEOUT_SECONDS",
)


def test_first_generation_files_and_route_are_absent() -> None:
    for relative_path in REMOVED_FILES:
        assert not (REPOSITORY_ROOT / relative_path).exists()
    reporting = REPOSITORY_ROOT / "backend" / "src" / "lib" / "benchmarks" / "reporting"
    assert not reporting.exists() or not tuple(reporting.glob("*.py"))

    main_source = (REPOSITORY_ROOT / "backend" / "main.py").read_text()
    assert "src.api.admin import benchmarks_router" not in main_source
    assert 'include_router(admin_benchmarks_router' not in main_source


def test_first_generation_settings_and_profiles_are_absent() -> None:
    config_source = (
        BACKEND_SOURCE / "lib" / "openai_agents" / "config.py"
    ).read_text()
    for setting in REMOVED_SETTINGS:
        assert setting not in config_source

    profile_dir = REPOSITORY_ROOT / "packages" / "alliance" / "benchmarks" / "profiles"
    assert not profile_dir.exists() or not tuple(profile_dir.iterdir())


def test_public_benchmark_modules_do_not_import_removed_engines() -> None:
    removed_imports = (
        "benchmarks.adjudication",
        "benchmarks.loader",
        "benchmarks.reporting",
        "benchmarks.scoring",
        "benchmarks.service",
    )
    for path in BACKEND_SOURCE.rglob("*.py"):
        source = path.read_text()
        for removed_import in removed_imports:
            assert removed_import not in source, path
