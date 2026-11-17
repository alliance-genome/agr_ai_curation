"""canonical() is the one identity function for validation history bodies.

PostgreSQL JSONB rejects the NUL character (U+0000), so canonical() removes it
from every string and string key before hashing. The digest therefore names the
stored form, and an in-memory value that still holds NUL hashes the same as the
body that was saved for it.
"""

from hashlib import sha256
import json

from src.lib.curation_workspace.extraction_results import (
    _validated_inline_domain_envelope_payload,
    canonical_extraction_payload_hash,
)
from src.lib.validation_history.store import canonical


SYNTHETIC = "Synthetic marker +/\x00 sentence for a null-character regression."
STRIPPED = "Synthetic marker +/ sentence for a null-character regression."


def _pre_fix_digest(value):
    """The exact canonical encoding used before NUL removal existed."""
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(encoded.encode()).hexdigest(), json.loads(encoded)


def test_content_without_nul_keeps_its_pre_fix_digest_and_snapshot():
    value = {
        "normalized": {"items": [{"label": "α", "quote": "plain +/- text"}], "n": 1.5},
        "typed": None,
        "flags": [True, False, None],
    }
    assert canonical(value) == _pre_fix_digest(value)


def test_nul_is_removed_from_nested_strings_lists_and_keys():
    value = {
        "outer\x00key": {
            "list": [SYNTHETIC, {"inner": [SYNTHETIC, 3, None]}],
            "\x00": "only-nul-key",
        },
        "tuple": (SYNTHETIC,),
    }
    digest, snapshot = canonical(value)
    assert snapshot == {
        "outerkey": {
            "list": [STRIPPED, {"inner": [STRIPPED, 3, None]}],
            "": "only-nul-key",
        },
        "tuple": [STRIPPED],
    }
    assert "\\u0000" not in json.dumps(snapshot)
    assert digest == _pre_fix_digest(snapshot)[0]


def test_unsanitized_value_and_its_stored_snapshot_share_one_identity():
    value = {"normalized": {"evidence": [{"verified_quote": SYNTHETIC}]}}
    digest, snapshot = canonical(value)
    # Re-recording the same content, or re-hashing the stored body, yields the
    # same identity, so idempotent writes and expected-digest checks agree.
    assert canonical(value) == (digest, snapshot)
    assert canonical(snapshot) == (digest, snapshot)


def test_history_identity_matches_extraction_result_persistence():
    envelope = {
        "envelope_id": "envelope-synthetic",
        "domain_pack_id": "fixture",
        "extracted_objects": [
            {
                "object_type": "fixture_object",
                "pending_ref_id": "pending-1",
                "payload": {"mention": SYNTHETIC},
            }
        ],
        "metadata": {"evidence_records": [{"verified_quote": SYNTHETIC}]},
    }
    # Extraction results store this sanitized payload; history stores canonical().
    persisted = _validated_inline_domain_envelope_payload(envelope)
    assert persisted["metadata"]["evidence_records"][0]["verified_quote"] == STRIPPED
    assert persisted["extracted_objects"][0]["payload"]["mention"] == STRIPPED
    assert canonical(persisted)[1] == persisted
    # Both hash the sanitized form with the same encoding.
    assert canonical_extraction_payload_hash(envelope) == canonical(envelope)[0]
