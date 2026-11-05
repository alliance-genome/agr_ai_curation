"""A pack may declare the exact field values for which a record needs no evidence."""

from types import SimpleNamespace

import pytest

from src.lib.domain_packs.evidence_policy import records_may_omit_evidence


def _pack(**object_metadata):
    return SimpleNamespace(
        object_definitions=[SimpleNamespace(object_type="tag", metadata=object_metadata)]
    )


RULE = {"evidence_optional_when": {"absent": True}}


@pytest.mark.parametrize("key", ["extracted_objects", "curatable_objects"])
def test_records_matching_the_declared_values_may_omit_evidence(key):
    payload = {key: [{"object_type": "tag", "payload": {"absent": True}}]}

    assert records_may_omit_evidence(payload, _pack(**RULE)) is True


@pytest.mark.parametrize(
    "objects",
    [
        pytest.param([], id="no records"),
        pytest.param([{"object_type": "tag", "payload": {"absent": False}}], id="other value"),
        pytest.param([{"object_type": "tag", "payload": {"absent": 1}}], id="same truth, other type"),
        pytest.param([{"object_type": "tag", "payload": {}}], id="field missing"),
        pytest.param(
            [
                {"object_type": "tag", "payload": {"absent": True}},
                {"object_type": "tag", "payload": {"absent": False}},
            ],
            id="one record does not match",
        ),
        pytest.param([{"object_type": "other", "payload": {"absent": True}}], id="undeclared type"),
    ],
)
def test_every_other_result_still_needs_evidence(objects):
    assert records_may_omit_evidence({"extracted_objects": objects}, _pack(**RULE)) is False


def test_a_pack_that_declares_nothing_always_needs_evidence():
    payload = {"extracted_objects": [{"object_type": "tag", "payload": {"absent": True}}]}

    assert records_may_omit_evidence(payload, _pack()) is False


def test_an_empty_declaration_is_not_a_blanket_exception():
    payload = {"extracted_objects": [{"object_type": "tag", "payload": {}}]}

    assert records_may_omit_evidence(payload, _pack(evidence_optional_when={})) is False
