import pytest

from src.lib.flow_transfer.reasons import REASON_CODES, reason_for_finding, reason_for_save_refusal


@pytest.mark.parametrize("code,reason", [
    ("unavailable_model", "model_unavailable"),
    ("unsupported_reasoning_effort", "model_unavailable"),
    ("extraction_identity_lookup_tools", "lookup_tools"),
    ("missing_execution_revision", "step_not_saved"),
    ("unavailable_execution_revision", "step_unavailable"),
    ("execution_contract_mismatch", "step_unavailable"),
    ("invalid_selected_export", "fields_need_choosing"),
    ("unavailable_projection_profile", "fields_need_choosing"),
    ("direct_export_requires_fields", "fields_need_choosing"),
    ("something_new", "cannot_run"),
])
def test_finding_codes_map_to_plain_reasons(code, reason):
    assert reason_for_finding(code) == reason


def test_a_save_refusal_uses_its_first_error_finding():
    detail = {"findings": [{"code": "x", "severity": "warning"},
                           {"code": "unavailable_model", "severity": "error"},
                           {"code": "invalid_selected_export", "severity": "error"}]}
    assert reason_for_save_refusal(detail) == "model_unavailable"
    assert reason_for_save_refusal("Persisted flow is not migratable") == "cannot_run"
    assert reason_for_save_refusal({"findings": []}) == "cannot_run"


def test_there_are_exactly_the_eight_reasons_of_the_spec():
    assert REASON_CODES == ("model_unavailable", "lookup_tools", "step_unavailable", "step_not_saved",
                            "fields_need_choosing", "flexible_output", "too_large", "cannot_run")
