"""Typed operational review outcomes, independent of scientific resolution."""


def status_for_reason(reason):
    if reason in {"cancelled", "interrupted", "flow_step_already_submitted"}:
        return "interrupted"
    if reason in {"budget_exhausted", "expired", "reviewer_turns_exhausted",
                  "validator_turns_exhausted", "scientific_attempts_exhausted",
                  "extraction_attempts_exhausted"}:
        return "review_exhausted"
    return "review_pending"
