"""Application-owned communication contract, independent of curator prompt text."""

VALIDATOR_FOLLOW_UP_GUIDANCE = (
    "VALIDATOR FOLLOW-UP CONTRACT: unresolved_questions is an optional list of useful "
    "follow-up questions, notes or qualifications for the calling extractor/agent. "
    "Leave it empty unless a specific uncertainty or missing context could materially "
    "help the caller decide what to do next; do not add routine caveats or repeat your "
    "explanation. You may report your best supported resolved judgment and still include "
    "such a note. State the uncertainty and what additional context would help, without "
    "inventing evidence. Questions alone do not make output incomplete. The caller, not "
    "a programmatic rule, decides whether further investigation or redispatch is useful."
)

EXTRACTOR_FOLLOW_UP_GUIDANCE = (
    "VALIDATOR FOLLOW-UP CONTRACT: validator unresolved_questions may contain optional "
    "questions, notes or qualifications alongside either resolved or unresolved judgments. "
    "They are context for your judgment, not a failure flag or an automatic retry request. "
    "When the validator handoff reports follow-up questions, inspect the exact stored "
    "validator result using its supplied read reference before deciding whether they matter. "
    "Use the paper and current task to decide whether to proceed with the supported result, "
    "seek specific missing context and redispatch, or retain uncertainty for the curator. "
    "Redispatch only when useful new context or evidence could change the assessment; "
    "do not repeat the same call merely because a note exists. Preserve meaningful "
    "uncertainty and do not silently relabel the validator's scientific judgment. "
    "Structural output errors remain separate and must not be treated as complete results."
)
