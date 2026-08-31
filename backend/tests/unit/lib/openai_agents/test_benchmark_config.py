from src.lib.openai_agents import config


def test_benchmark_operational_defaults(monkeypatch):
    for key in (
        "BENCHMARK_ENABLED",
        "BENCHMARK_MAX_CONCURRENCY",
        "BENCHMARK_MATRIX_LIMIT",
        "BENCHMARK_CASE_LIMIT",
        "BENCHMARK_RESULT_LIMIT",
        "BENCHMARK_TIMEOUT_SECONDS",
        "BENCHMARK_RETRIES",
        "BENCHMARK_PREVIEW_MAX_CHARS",
        "BENCHMARK_INLINE_MAX_BYTES",
    ):
        monkeypatch.delenv(key, raising=False)

    assert config.get_benchmark_enabled() is False
    assert config.get_benchmark_max_concurrency() == 2
    assert config.get_benchmark_matrix_limit() == 20
    assert config.get_benchmark_case_limit() == 20
    assert config.get_benchmark_result_limit() == 20
    assert config.get_benchmark_timeout_seconds() == 300
    assert config.get_benchmark_retries() == 0
    assert config.get_benchmark_preview_max_chars() == 1000
    assert config.get_benchmark_inline_max_bytes() == 20000


def test_benchmark_operational_overrides_are_bounded(monkeypatch):
    monkeypatch.setenv("BENCHMARK_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_MAX_CONCURRENCY", "0")
    monkeypatch.setenv("BENCHMARK_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("BENCHMARK_RETRIES", "-2")

    assert config.get_benchmark_enabled() is True
    assert config.get_benchmark_max_concurrency() == 1
    assert config.get_benchmark_timeout_seconds() == 0.1
    assert config.get_benchmark_retries() == 0
