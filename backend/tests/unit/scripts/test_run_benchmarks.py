from types import SimpleNamespace

from src.lib.benchmarks.models import DryRunPlan
import src.lib.benchmarks.cli as run_benchmarks


def test_cli_validation_targets_profile_case_and_route_without_execution(
    monkeypatch, capsys
):
    captured = {}

    class Service:
        def plan(self, selection):
            captured["selection"] = selection
            return DryRunPlan(runs=[])

    monkeypatch.setattr(run_benchmarks, "build_default_service", Service)
    result = run_benchmarks.main(
        [
            "--validate",
            "--profile",
            "profile-1",
            "--case",
            "case-1",
            "--provider",
            "openrouter",
            "--model",
            "deepseek/deepseek-v4-pro-0813",
        ]
    )

    assert result == 0
    assert captured["selection"].profile_ids == ["profile-1"]
    assert captured["selection"].case_ids == ["case-1"]
    assert captured["selection"].route.provider == "openrouter"
    assert '"runs": []' in capsys.readouterr().out


def test_cli_execution_requires_feature_gate(monkeypatch, capsys):
    monkeypatch.setattr(
        run_benchmarks, "build_default_service", lambda: SimpleNamespace()
    )
    monkeypatch.setattr(run_benchmarks, "get_benchmark_enabled", lambda: False)

    assert run_benchmarks.main([]) == 2
    assert "disabled" in capsys.readouterr().err


def test_cli_returns_failure_when_any_case_fails(monkeypatch):
    failed_run = SimpleNamespace(status="failed")

    class Response:
        runs = [failed_run]

        def model_dump_json(self, **_kwargs):
            return "{}"

    class Service:
        async def execute(self, _selection):
            return Response()

    monkeypatch.setattr(run_benchmarks, "build_default_service", Service)
    monkeypatch.setattr(run_benchmarks, "get_benchmark_enabled", lambda: True)

    assert run_benchmarks.main([]) == 1


def test_cli_rejects_partial_route_override(capsys):
    assert run_benchmarks.main(["--provider", "openai", "--validate"]) == 2
    assert "supplied together" in capsys.readouterr().err
