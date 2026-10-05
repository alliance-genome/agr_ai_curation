"""Why a flow can't be imported, as one plain code; curators read the portal's fixed sentence."""

from typing import Literal, get_args

from src.lib.benchmarks.saved_flows import PROJECTION_CODES

ReasonCode = Literal[
    "model_unavailable", "lookup_tools", "step_unavailable", "step_not_saved",
    "fields_need_choosing", "flexible_output", "too_large", "cannot_run",
]
REASON_CODES: tuple[str, ...] = get_args(ReasonCode)


def reason_for_finding(code: str) -> ReasonCode:
    """The reason for one blocking finding code; a finding's own message is never used."""
    if code in ("unavailable_model", "unsupported_reasoning_effort"):
        return "model_unavailable"
    if code == "extraction_identity_lookup_tools":
        return "lookup_tools"
    if code == "missing_execution_revision":
        return "step_not_saved"
    if code in ("unavailable_execution_revision", "execution_contract_mismatch"):
        return "step_unavailable"
    if code in PROJECTION_CODES:
        return "fields_need_choosing"
    return "cannot_run"


def reason_for_save_refusal(detail: object) -> ReasonCode:
    """A flow save's 422 detail: its first error finding's reason, else ``cannot_run``."""
    if isinstance(detail, dict):
        for finding in detail.get("findings") or []:
            if isinstance(finding, dict) and finding.get("severity") == "error":
                return reason_for_finding(str(finding.get("code")))
    return "cannot_run"
