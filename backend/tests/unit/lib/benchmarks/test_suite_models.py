import pytest
from pydantic import ValidationError

from src.lib.benchmarks.models import BenchmarkSuite


def _suite_payload() -> dict:
    return {
        "schema_version": 2,
        "suite_id": "suite-1",
        "cases": [
            {
                "case_id": "case-1",
                "target": {"kind": "agent", "id": "extractor"},
                "input": {
                    "resolver": "checked_in_fixture",
                    "reference": "cases/case-1/input.json",
                    "version": "1",
                    "digest": "sha256:" + "a" * 64,
                },
            }
        ],
        "configurations": [
            {
                "configuration_id": "arm-1",
                "routes": {
                    "agent:extractor": {
                        "provider": "openai",
                        "model": "model-a",
                        "reasoning_effort": "high",
                    }
                },
            }
        ],
    }


def test_suite_v2_is_strict_and_immutable():
    suite = BenchmarkSuite.model_validate(_suite_payload())

    assert suite.repetitions == 1
    with pytest.raises(ValidationError, match="frozen"):
        suite.suite_id = "changed"


@pytest.mark.parametrize("field", ["expected", "gold", "scorers", "adjudicator"])
def test_suite_v2_rejects_biological_correctness_fields(field):
    payload = _suite_payload()
    payload[field] = {}

    with pytest.raises(ValidationError, match=field):
        BenchmarkSuite.model_validate(payload)


def test_suite_v2_rejects_request_time_urls_and_unversioned_inputs():
    payload = _suite_payload()
    payload["cases"][0]["input"] = {"url": "https://example.org/paper.pdf"}

    with pytest.raises(ValidationError, match="url|resolver"):
        BenchmarkSuite.model_validate(payload)

    payload = _suite_payload()
    payload["cases"][0]["input"]["reference"] = "https://example.org/paper.pdf"
    with pytest.raises(ValidationError, match="network URLs"):
        BenchmarkSuite.model_validate(payload)


def test_suite_v2_rejects_implicit_route_lists():
    payload = _suite_payload()
    payload["configurations"][0]["routes"] = [
        {"provider": "openai", "model": "model-a"},
        {"provider": "openai", "model": "model-b"},
    ]

    with pytest.raises(ValidationError, match="routes"):
        BenchmarkSuite.model_validate(payload)
