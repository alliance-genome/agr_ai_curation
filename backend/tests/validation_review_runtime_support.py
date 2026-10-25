"""Scripted provider boundary for real validation-review SDK tool loops.

This helper does not supply decisions or inspect persistence itself. Subclasses
implement ``review`` as a generator of SDK responses, receiving actual tool
results and using ``read`` to consume complete, hash-pinned history pages.
It is wiring-test scaffolding, not a model-quality or token-measurement oracle.
"""

import ast
import json

from tests.unit.lib.openai_agents.test_model_request_measurement import (
    FakeResponsesWSModel,
    _tool_call,
)


class HistoryReadingReviewer(FakeResponsesWSModel):
    """Script provider replies from real tool outputs, never from a fake ledger."""

    def __init__(self):
        super().__init__()
        self.script = self.review()
        self.read_bodies = []
        self.read_pages = 0
        self.pending_call_id = None

    def _next(self, kwargs):
        self.calls.append(kwargs)
        from src.lib.validation_review.tools import runtime_tool_contract
        offered = {tool.name for tool in kwargs["tools"]}
        allowed = {tool["name"] for tool in runtime_tool_contract()}
        core = {"get_validation_review_packet", "read_validation_history", "record_validation_review",
                "request_validation_reconsideration", "get_validation_review_status"}
        from src.lib.openai_agents.run_history import HISTORY_TOOL_NAMES
        assert offered & HISTORY_TOOL_NAMES in (set(), HISTORY_TOOL_NAMES), offered
        assert offered - HISTORY_TOOL_NAMES in (core, allowed), offered
        if len(self.calls) == 1:
            response = next(self.script)
        else:
            outputs = [item for item in kwargs["input"]
                       if isinstance(item, dict)
                       and item.get("type") == "function_call_output"
                       and item.get("call_id") == self.pending_call_id]
            assert len(outputs) == 1, "Expected exactly the outstanding tool response"
            output = outputs[0]["output"]
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except json.JSONDecodeError:
                    output = ast.literal_eval(output)
            try:
                response = self.script.send(output)
            except StopIteration as error:
                raise AssertionError("Unexpected extra reviewer model turn after script completion") from error
        calls = [item for item in response.output if item.type == "function_call"]
        assert len(calls) <= 1, "This provider script issues one tool call per turn"
        self.pending_call_id = calls[0].call_id if calls else None
        return response

    def read(self, reference):
        offset, chunks = 0, []
        while True:
            page = yield _tool_call("read_validation_history", f"read-{len(self.calls)}", json.dumps({
                "fact_id": reference.get("fact_id", reference.get("id")),
                "sha256": reference["sha256"], "offset": offset,
            }))
            assert page["offset"] == offset
            self.read_pages += 1
            chunks.append(page["content"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        body = json.loads("".join(chunks))
        self.read_bodies.append(body)
        return body



class CanonicalReviewer(HistoryReadingReviewer):
    """Identical grouped-review/retry judgments for every runtime adapter."""

    unresolved_b = False
    retry_guidance = "Recheck C against the supplied original and explain the previous ambiguity."

    def review(self):
        reference = yield _tool_call(
            "get_validation_review_packet", "initial-packet", '{"operation_id":"canonical-initial"}'
        )
        packet = yield from self.read(reference)
        original = yield from self.read(packet["baseline"])
        for label in "ABC":
            assert f"eco:{label}" in json.dumps(original)
        entries = []
        rejected_target = None
        self.initial_answers = {}
        for unit in packet["manifest"]["units"]:
            attempt, answer = yield from self.latest_answer(packet, unit)
            body = json.dumps(answer)
            if self.unresolved_b and answer["result"]["status"] == "unresolved":
                assert answer["result"]["resolved_values"] == {}
                assert "Unresolved B" in answer["result"]["explanation"]
                labels = ["B"]
            else:
                labels = [label for label in "ABC" if f"FIRST_{label}" in body]
            assert len(labels) == 1, body
            label = labels[0]
            assert label not in self.initial_answers
            self.initial_answers[label] = attempt["id"]
            if label == "C":
                rejected_target = unit["target_id"]
            entries.append(self.entry(unit, attempt, "reject" if label == "C" else "accept"))
        assert set(self.initial_answers) == set("ABC")
        decision = yield _tool_call("record_validation_review", "grouped-review", json.dumps({
            "operation_id": "canonical-grouped-review", "entries": entries,
        }))
        assert not decision["ready"]
        self.parent_decision_id = decision["decision_id"]
        retry = yield _tool_call("request_validation_reconsideration", "reconsider-C", json.dumps({
            "operation_id": "canonical-reconsider-C",
            "parent_decision_id": self.parent_decision_id,
            "target_ids": [rejected_target],
            "instructions": self.retry_guidance,
            "evidence_refs": [{"fact_id": packet["baseline"]["fact_id"],
                               "sha256": packet["baseline"]["sha256"]}],
        }))
        assert retry["coverage"] == [rejected_target]
        reference = yield _tool_call(
            "get_validation_review_packet", "retry-packet", '{"operation_id":"canonical-after-retry"}'
        )
        current = yield from self.read(reference)
        assert current["episode_id"] == packet["episode_id"]
        unit = next(unit for unit in current["manifest"]["units"]
                    if unit["target_id"] == rejected_target)
        attempt, answer = yield from self.latest_answer(current, unit)
        assert attempt["id"] != self.initial_answers["C"]
        assert "SECOND_C" in json.dumps(answer)
        self.final_attempt_id = attempt["id"]
        yield _tool_call("record_validation_review", "accept-C2", json.dumps({
            "operation_id": "canonical-accept-C2", "entries": [self.entry(unit, attempt, "accept")],
        }))
        # The runtime may stop immediately when the last explicit decision makes
        # the ledger ready. Assertions use persisted state, not receipt of prose.
        from tests.unit.lib.openai_agents.test_model_request_measurement import _final
        yield _final("All three saved answers reviewed.")

    def latest_answer(self, packet, unit):
        attempts = sorted((fact for fact in packet["facts"]
            if fact["kind"] == "attempt" and fact["target_id"] == unit["target_id"]),
            key=lambda fact: fact["sequence"], reverse=True)
        assert attempts
        attempt = attempts[0]
        body = yield from self.read(attempt)
        assert body["input"]["review_episode_id"] == packet["episode_id"]
        completion = next(fact for fact in packet["facts"]
            if fact["kind"] == "completion" and fact["parent_id"] == attempt["id"])
        answer = yield from self.read(completion)
        return attempt, answer

    @staticmethod
    def entry(unit, attempt, action):
        return {
            "target_id": unit["target_id"], "decision_unit_id": unit["target_id"],
            "expected_version": unit["version"], "logical_attempt_id": attempt["id"],
            "extraction_ref": None, "action": action,
            "reason": "Ambiguous C requires reconsideration" if action == "reject"
                      else "The exact saved answer is sufficient, including any caveat",
        }


class CanonicalValidator(FakeResponsesWSModel):
    """Script only the provider reply; retain the real validator finalizer."""

    unresolved_b = False

    def _next(self, kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 2:
            from tests.unit.lib.openai_agents.test_model_request_measurement import _final
            outputs = [item for item in kwargs["input"] if item.get("type") == "function_call_output"
                       and item.get("call_id") == "validator-answer"]
            assert len(outputs) == 1
            feedback = outputs[0]["output"]
            if isinstance(feedback, str):
                feedback = ast.literal_eval(feedback)
            assert feedback["status"] == "accepted", feedback
            return _final(json.dumps(self.answer))
        assert len(self.calls) == 1, "Unexpected validator turn after finalization and final answer"
        raw = kwargs["input"]
        if isinstance(raw, list):
            raw = next(item["content"] for item in raw if item.get("role") == "user")
        if isinstance(raw, list):
            raw = "".join(item["text"] for item in raw)
        payload = json.loads(raw)
        self.received_payload = payload
        labels = [label for label in "ABC" if f"eco:{label}" in json.dumps(payload["selected_inputs"])]
        assert len(labels) == 1, payload["selected_inputs"]
        label = labels[0]
        reconsideration = payload.get("validation_reconsideration")
        retry = reconsideration
        if reconsideration:
            context = reconsideration["specialist_context"]
            ordinals = {item["ordinal"] for item in context["attempt"]["targets"].values()}
            if ordinals == {1}:
                # A curator's first explicit round carries guidance/history too;
                # it is not a second scientific attempt just because it has context.
                assert not context["previous_validation"]
                assert context["supervisor_request"]["parent_kind"] == "workspace_revalidation_request"
                assert "Review all three original records" in json.dumps(
                    context["supervisor_request"]["parent_decision"]["entries"])
                self.curator_initial_context = reconsideration
                retry = None
        if retry:
            assert label == "C"
            assert retry["instructions"] == CanonicalReviewer.retry_guidance
            context = retry["specialist_context"]
            assert len(context["original_entries"]) == 1
            assert "eco:C" in json.dumps(context["original_entries"])
            assert "FIRST_C" in json.dumps(context["previous_validation"])
            assert "FIRST_A" not in json.dumps(context["previous_validation"])
            assert "FIRST_B" not in json.dumps(context["previous_validation"])
            assert "Ambiguous C requires reconsideration" in json.dumps(context["decisions"])
            assert list(context["attempt"]["targets"].values()) == [
                {"ordinal": 2, "ceiling": 2, "remaining_after": 0}]
            assert payload["supplied_history"]["reader"] == "read_supplied_history"
            assert any(tool.name == "read_supplied_history" for tool in kwargs["tools"])
            self.retry_history = payload["supplied_history"]
        result_keys = set(payload["expected_result_fields"])
        assert result_keys in ({"curie"}, {"identifier"}), result_keys
        result = {
            "status": "resolved", "request_id": payload["request_id"],
            "validator_binding_id": payload["validator_binding_id"],
            "validator_agent": payload["validator_agent"], "target": payload["target"],
            "resolved_values": {next(iter(result_keys)): "SECOND_C" if retry else f"FIRST_{label}"},
            "resolved_objects": [], "missing_expected_fields": [], "candidates": [],
            "lookup_attempts": [], "curator_message": None,
            "unresolved_questions": ["Supplied information has an ambiguity"] if label in "BC" and not retry else [],
            "explanation": "Scripted fixture answer based only on the supplied request.",
        }
        if self.unresolved_b and label == "B":
            assert not retry
            result.update(status="unresolved", resolved_values={},
                          missing_expected_fields=sorted(result_keys),
                          explanation="Unresolved B: supplied information does not support an identifier.")
        self.answer = result
        return _tool_call("finalize_validator_result", "validator-answer", json.dumps({"result": result}))
