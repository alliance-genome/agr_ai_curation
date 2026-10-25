"""Frozen, environment-backed review limits. These never judge scientific content."""

from dataclasses import asdict, dataclass
from src.lib.openai_agents.config import get_validation_review_limits


@dataclass(frozen=True)
class ReviewLimits:
    attempts_per_target: int
    extraction_attempts_per_entry: int
    extra_per_run: int
    extra_per_batch: int
    reviewer_turns: int
    reviewer_tools: int
    deadline_seconds: int
    reviewer_tokens: int
    tokens_per_target: int
    tokens_per_extra: int
    warning_percent: int
    critical_percent: int
    instruction_chars: int
    decision_entries: int
    evidence_refs: int
    packet_bytes: int
    lease_seconds: int

    @classmethod
    def configured(cls):
        return cls(**get_validation_review_limits())

    def snapshot(self):
        return asdict(self)

    def allowances(self, *, targets: int, episodes: int, extras: int):
        if min(targets, episodes, extras) < 0:
            raise ValueError("Budget cardinalities must be nonnegative")
        return {
            "initial_tokens": targets * self.tokens_per_target,
            "reconsideration_tokens": extras * self.tokens_per_extra,
            "reviewer_tokens": episodes * self.reviewer_tokens,
            "extra_attempts": extras,
            "reviewer_turns": episodes * self.reviewer_turns,
            "reviewer_tools": episodes * self.reviewer_tools,
        }
