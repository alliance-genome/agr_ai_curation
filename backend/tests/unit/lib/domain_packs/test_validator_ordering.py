"""Validator ordering (``runs_after``) and optional result fields, on a synthetic pack."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationRegistryError,
    validator_dispatch_waves,
)
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope


@pytest.fixture(autouse=True)
def _runtime_packages(monkeypatch):
    from ..packages import find_repo_root
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(find_repo_root(Path(__file__)) / "packages"))


def _binding(binding_id: str, *, extra: str = "", state_object: str = "Claim") -> str:
    return f"""
      - binding_id: {binding_id}
        display_name: {binding_id}
        validator_agent:
          package_id: fixture.validators
          agent_id: {binding_id.split('.')[-1]}_validator
        applies_to:
          domain_pack_id: fixture.ordering
          object_types:
            - {state_object}
{extra}"""


def _pack(tmp_path: Path, bindings: str, *, under_development: str = "") -> LoadedDomainPack:
    text = f"""
pack_id: fixture.ordering
display_name: Fixture Ordering Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: ClaimPayload
    display_name: Claim payload
object_definitions:
  - object_type: Claim
    display_name: Claim
    model_ref: ClaimPayload
    fields:
      - field_path: term.curie
        field_type: string
      - field_path: term.label
        field_type: string
      - field_path: term.xref
        field_type: string
      - field_path: verdict
        field_type: string
  - object_type: Note
    display_name: Note
    fields:
      - field_path: text
        field_type: string
metadata:
  validator_bindings:
    active:{bindings}
{under_development}""".strip()
    pack_path = tmp_path / "fixture.ordering"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(text, encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


_LOOKUP_FIELDS = """        input_fields:
          label:
            source: payload
            path: term.label
        expected_result_fields:
          curie: term.curie
"""


def _check_fields(runs_after: str = "") -> str:
    return f"""{runs_after}        input_fields:
          curie:
            source: payload
            path: term.curie
            required: false
          label:
            source: payload
            path: term.label
        expected_result_fields:
          verdict: verdict
"""


_RUNS_AFTER_LOOKUP = """        runs_after:
          - fixture.lookup
"""


def _envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="ordering-env",
        domain_pack_id="fixture.ordering",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Claim",
                pending_ref_id="claim-1",
                payload={"term": {"curie": None, "label": "a term"}, "verdict": None},
            )
        ],
    )


def _result(request, *, status: str, resolved_values: dict[str, Any], outcome: str) -> dict[str, Any]:
    return {
        "status": status,
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": resolved_values,
        "resolved_objects": [],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "fixture_lookup",
                "method": "search",
                "query": dict(request.selected_inputs),
                "result_count": 1 if outcome == "success" else 0,
                "outcome": outcome,
            }
        ],
        "curator_message": None,
        "explanation": "Fixture validator result.",
    }


def _runner(seen: list[tuple[str, dict[str, Any]]], *, lookup_outcome: str = "success"):
    def run(request, *, binding):
        seen.append((binding.binding_id, dict(request.selected_inputs)))
        if binding.binding_id == "fixture.lookup":
            if lookup_outcome != "success":
                return _result(request, status="unresolved", resolved_values={}, outcome=lookup_outcome)
            return _result(request, status="resolved", resolved_values={"curie": "T:0001"}, outcome="success")
        return _result(
            request,
            status="resolved",
            resolved_values={"verdict": f"checked {request.selected_inputs.get('curie')}"},
            outcome="success",
        )

    return run


# --- Load-time checks ---------------------------------------------------------


def test_runs_after_orders_bindings_into_waves(tmp_path):
    pack = _pack(
        tmp_path,
        _binding("fixture.lookup", extra=_LOOKUP_FIELDS)
        + _binding("fixture.check", extra=_check_fields(_RUNS_AFTER_LOOKUP)),
    )
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    bindings = {binding.binding_id: binding for binding in registry.bindings}

    assert bindings["fixture.check"].runs_after == ("fixture.lookup",)
    assert validator_dispatch_waves(registry.bindings) == (
        frozenset({"fixture.lookup"}),
        frozenset({"fixture.check"}),
    )


@pytest.mark.parametrize(
    ("bindings", "under_development", "message"),
    [
        (
            _binding("fixture.check", extra=_check_fields("        runs_after:\n          - fixture.missing\n")),
            "",
            "runs after unknown or inactive binding 'fixture.missing'",
        ),
        (
            _binding("fixture.check", extra=_check_fields("        runs_after:\n          - fixture.check\n")),
            "",
            "cannot run after itself",
        ),
        (
            _binding("fixture.lookup", extra=_LOOKUP_FIELDS + "        runs_after:\n          - fixture.check\n")
            + _binding("fixture.check", extra=_check_fields(_RUNS_AFTER_LOOKUP)),
            "",
            "form a cycle: fixture.check -> fixture.lookup -> fixture.check",
        ),
        (
            _binding("fixture.check", extra=_check_fields("        runs_after:\n          - fixture.planned\n")),
            """    under_development:
      - binding_id: fixture.planned
        display_name: Planned
        state_explanation: Not built yet.
""",
            "runs after unknown or inactive binding 'fixture.planned'",
        ),
        (
            _binding(
                "fixture.note_lookup",
                state_object="Note",
                extra="""        input_fields:
          text:
            source: payload
            path: text
        expected_result_fields:
          text: text
""",
            )
            + _binding(
                "fixture.check",
                extra=_check_fields("        runs_after:\n          - fixture.note_lookup\n"),
            ),
            "",
            "targets none of its object types",
        ),
    ],
)
def test_runs_after_problems_fail_at_load(tmp_path, bindings, under_development, message):
    pack = _pack(tmp_path, bindings, under_development=under_development)

    with pytest.raises(ValidationRegistryError, match=message):
        DomainPackValidationRegistry.from_domain_pack(pack)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (_LOOKUP_FIELDS + "        runs_after: []\n", "runs_after must list at least one binding_id"),
        (
            _LOOKUP_FIELDS + "        optional_result_fields:\n          curie: term.curie\n",
            "optional_result_fields cannot repeat expected_result_fields: curie",
        ),
    ],
)
def test_binding_schema_rejects_empty_ordering_and_duplicated_optional_fields(tmp_path, extra, message):
    with pytest.raises(ValueError, match=message):
        _pack(tmp_path, _binding("fixture.lookup", extra=extra))


# --- Dispatch ------------------------------------------------------------------


def test_dependent_binding_reads_the_values_its_prerequisite_wrote(tmp_path):
    pack = _pack(
        tmp_path,
        _binding("fixture.lookup", extra=_LOOKUP_FIELDS)
        + _binding("fixture.check", extra=_check_fields(_RUNS_AFTER_LOOKUP)),
    )
    seen: list[tuple[str, dict[str, Any]]] = []

    result = dispatch_active_validator_bindings(_envelope(), pack, runner=_runner(seen))

    assert [binding_id for binding_id, _inputs in seen] == ["fixture.lookup", "fixture.check"]
    assert seen[1][1]["curie"] == "T:0001"
    payload = result.envelope.extracted_objects[0].payload
    assert payload["verdict"] == "checked T:0001"
    assert len(result.validator_results) == 2


def test_without_runs_after_both_bindings_read_the_extracted_values(tmp_path):
    pack = _pack(
        tmp_path,
        _binding("fixture.lookup", extra=_LOOKUP_FIELDS) + _binding("fixture.check", extra=_check_fields()),
    )
    seen: list[tuple[str, dict[str, Any]]] = []

    dispatch_active_validator_bindings(_envelope(), pack, runner=_runner(seen))

    assert dict(seen)["fixture.check"].get("curie") is None


@pytest.mark.parametrize("outcome", ["error", "blocked"])
def test_a_prerequisite_that_fails_non_decisively_still_lets_the_dependent_run(tmp_path, outcome):
    pack = _pack(
        tmp_path,
        _binding("fixture.lookup", extra=_LOOKUP_FIELDS)
        + _binding("fixture.check", extra=_check_fields(_RUNS_AFTER_LOOKUP)),
    )
    seen: list[tuple[str, dict[str, Any]]] = []

    result = dispatch_active_validator_bindings(
        _envelope(), pack, runner=_runner(seen, lookup_outcome=outcome),
    )

    assert [binding_id for binding_id, _inputs in seen] == ["fixture.lookup", "fixture.check"]
    assert seen[1][1].get("curie") is None
    assert [item.status for item in result.validator_results] == ["unresolved", "resolved"]


# --- Optional result fields ------------------------------------------------------


_OPTIONAL_LOOKUP = _LOOKUP_FIELDS + """        optional_result_fields:
          xref: term.xref
"""


@pytest.mark.parametrize(
    ("resolved_values", "expected_xref"),
    [
        ({"curie": "T:0001", "xref": "X:9"}, "X:9"),
        ({"curie": "T:0001"}, None),
    ],
)
def test_optional_result_fields_are_written_when_returned_and_never_demote(
    tmp_path, resolved_values, expected_xref,
):
    pack = _pack(tmp_path, _binding("fixture.lookup", extra=_OPTIONAL_LOOKUP))
    captured = {}

    def run(request, *, binding):
        captured["request"] = request
        return _result(request, status="resolved", resolved_values=resolved_values, outcome="success")

    result = dispatch_active_validator_bindings(_envelope(), pack, runner=run)

    request = captured["request"]
    assert request.target.optional_fields == ["xref"]
    assert request.optional_result_fields == {"xref": "term.xref"}
    assert request.expected_result_fields == {"curie": "term.curie"}
    assert result.validator_results[0].status == "resolved"
    term = result.envelope.extracted_objects[0].payload["term"]
    assert term["curie"] == "T:0001"
    assert term.get("xref") == expected_xref


def test_a_binding_without_optional_fields_keeps_its_request_shape(tmp_path):
    pack = _pack(tmp_path, _binding("fixture.lookup", extra=_LOOKUP_FIELDS))
    captured = {}

    def run(request, *, binding):
        captured["request"] = request
        return _result(request, status="resolved", resolved_values={"curie": "T:0001"}, outcome="success")

    dispatch_active_validator_bindings(_envelope(), pack, runner=run)

    request = captured["request"]
    assert request.optional_result_fields is None
    assert "optional_fields" not in request.target.model_dump(mode="json", exclude_none=True)


def test_a_later_wave_target_missing_from_the_envelope_is_a_clear_error():
    """Core review nit: materialization never drops an object, so a later wave that cannot
    find its target names the binding and object instead of stopping on a bare StopIteration."""

    from types import SimpleNamespace

    from src.lib.domain_packs.validator_dispatch import _match_on_envelope

    match = SimpleNamespace(
        binding=SimpleNamespace(binding_id="fixture.check"),
        object_envelope=CuratableObjectEnvelope(object_type="Claim", pending_ref_id="claim-9", payload={}),
    )

    with pytest.raises(ValueError, match=r"'fixture.check' targets object \[\('pending_ref_id', 'claim-9'\)\]"):
        _match_on_envelope(match, _envelope())
