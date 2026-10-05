from copy import deepcopy

import pytest

from src.lib.flow_transfer import bundle as bundle_module
from src.lib.flow_transfer.bundle import (BundleTooLarge, InvalidBundle, bundle_sha256,
                                          check_bundle)

from .support import make_bundle


def test_a_valid_bundle_is_exactly_its_closure():
    raw = make_bundle()
    checked = check_bundle(raw)
    revision_id = checked.bundle.agents[0].revisions[0].source_revision_id
    assert checked.pins == {"node_0": (checked.bundle.agents[0].source_agent_id, revision_id)}
    assert set(checked.snapshots) == {revision_id}
    assert checked.receipts["node_0"]["agent_revision_id"] == str(revision_id)
    assert checked.sha256 == bundle_sha256(raw)


def test_a_bundle_with_an_output_structure_carries_exactly_that_revision():
    checked = check_bundle(make_bundle(with_profile=True))
    assert len(checked.bundle.profiles) == 1


def test_a_flow_of_only_standard_agents_is_a_valid_bundle():
    raw = make_bundle(with_agent=False)
    raw["flow"]["definition"]["nodes"].append({
        "id": "node_s", "type": "agent", "position": {"x": 1, "y": 1},
        "data": {"agent_id": "pdf_extraction", "agent_display_name": "PDF", "output_key": "pdf"}})
    raw["flow"]["definition"]["edges"].append({"id": "e_s", "source": "task", "target": "node_s"})
    checked = check_bundle(raw)
    assert checked.pins == {} and checked.bundle.agents == [] and checked.bundle.profiles == []


def test_the_hash_does_not_depend_on_key_order():
    raw = make_bundle()
    reordered = dict(reversed(list(deepcopy(raw).items())))
    assert bundle_sha256(raw) == bundle_sha256(reordered)


def _mutated(change):
    raw = make_bundle(with_profile=True)
    change(raw)
    return raw


@pytest.mark.parametrize("change", [
    lambda raw: raw.update(format_version=2),
    lambda raw: raw.update(unexpected=True),
    lambda raw: raw["agents"][0]["revisions"][0]["snapshot"].update(instructions="Changed"),
    lambda raw: raw["agents"][0]["revisions"].append(deepcopy(raw["agents"][0]["revisions"][0]) | {
        "source_revision_id": "00000000-0000-4000-8000-000000000001", "revision": 9}),
    lambda raw: raw["agents"].clear(),
    lambda raw: raw["profiles"].clear(),
    lambda raw: raw["profiles"][0]["revisions"][0]["contract"].update(name="Other"),
    lambda raw: raw["flow"]["definition"]["nodes"][1]["data"].pop("execution_receipt"),
    lambda raw: raw["flow"]["definition"]["nodes"][1]["data"]["execution_receipt"].update(revision=4),
    lambda raw: raw["flow"]["definition"].update(nodes=[]),
], ids=["format", "extra-key", "tampered-snapshot", "extra-revision", "missing-agent",
        "missing-profile", "tampered-profile", "unpinned-step", "receipt-mismatch", "no-nodes"])
def test_anything_but_the_exact_closure_is_refused(change):
    with pytest.raises(InvalidBundle):
        check_bundle(_mutated(change))


def test_an_oversized_bundle_is_refused(monkeypatch):
    monkeypatch.setattr(bundle_module, "FLOW_BUNDLE_MAX_BYTES", 100)
    with pytest.raises(BundleTooLarge):
        check_bundle(make_bundle())
