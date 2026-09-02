"""Contract: every active Alliance validator binding speaks curator voice (ALL-1026).

The Flow Builder node panel reads its switch sentences, descriptions, and
turn-off consequences straight from the domain packs, so each shipped pack
must carry the curator-facing keys on every active binding and every active
pack-level validator entry. ``when_off`` exists exactly where a curator may
turn the check off (``allow_opt_out``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry  # noqa: E402
from src.lib.domain_packs.validation_registry import (  # noqa: E402
    DomainPackValidationRegistry,
    ValidationBindingState,
)


# Developer wording that must not reach a curator-facing sentence.
DEVELOPER_WORDING = re.compile(
    r"\b(binding|package|LinkML|materializ\w*|payload|envelope|fixture|"
    r"dispatch|selector|schema|deterministic|provider-agnostic|"
    r"ALL-\d+|KANBAN-\d+|\bR\d\b|\bD\d\b)",
    re.IGNORECASE,
)
# Raw identifiers (snake_case paths, dotted ids) do not belong in a label.
RAW_IDENTIFIER = re.compile(r"\w+_\w+|\w+\.\w+")


def _registries() -> dict[str, DomainPackValidationRegistry]:
    alliance_registry = load_alliance_domain_pack_registry()
    return {
        pack_id: DomainPackValidationRegistry.from_domain_pack(pack)
        for pack_id, pack in sorted(alliance_registry.packs_by_id.items())
    }


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part])


def _assert_curator_sentence(text: str | None, *, where: str, max_sentences: int) -> None:
    assert text, f"{where}: missing curator-voice text"
    assert text == text.strip(), f"{where}: surrounding whitespace"
    assert not DEVELOPER_WORDING.search(text), f"{where}: developer wording in {text!r}"
    assert _sentence_count(text) <= max_sentences, f"{where}: too many sentences in {text!r}"


def _assert_curator_label(label: str | None, *, where: str) -> None:
    _assert_curator_sentence(label, where=where, max_sentences=1)
    assert label is not None
    assert label.startswith("Confirm "), f"{where}: curator_label must be imperative: {label!r}"
    assert not label.endswith("."), f"{where}: curator_label is a switch sentence without a period"
    assert not RAW_IDENTIFIER.search(label), f"{where}: identifier in curator_label {label!r}"


def test_shipped_packs_have_active_bindings():
    registries = _registries()
    active = [
        binding
        for registry in registries.values()
        for binding in registry.bindings
        if binding.state is ValidationBindingState.ACTIVE
    ]
    assert len(active) >= 30, "expected the Alliance packs to ship active bindings"
    assert any(binding.allow_opt_out for binding in active)
    assert any(not binding.allow_opt_out for binding in active)


@pytest.mark.parametrize("pack_id", sorted(_registries()))
def test_active_bindings_carry_curator_voice_keys(pack_id: str):
    registry = _registries()[pack_id]
    for binding in registry.bindings:
        where = f"{pack_id}:binding:{binding.binding_id}"
        if binding.state is not ValidationBindingState.ACTIVE:
            assert binding.curator_label is None, f"{where}: under-development bindings carry no switch text"
            assert binding.when_off is None, f"{where}: under-development bindings carry no when_off"
            continue

        _assert_curator_label(binding.curator_label, where=where)
        # ``reason`` is the active binding's description.
        _assert_curator_sentence(binding.reason, where=f"{where}:description", max_sentences=2)
        assert binding.reason is not None and binding.reason.endswith(".")

        if binding.allow_opt_out:
            _assert_curator_sentence(binding.when_off, where=f"{where}:when_off", max_sentences=1)
            assert binding.when_off is not None and binding.when_off.endswith(".")
        else:
            assert binding.when_off is None, f"{where}: when_off only on opt-out bindings"

        notes = binding.raw.get("definition_notes")
        assert isinstance(notes, list) and notes, f"{where}: developer detail belongs in definition_notes"


@pytest.mark.parametrize("pack_id", sorted(_registries()))
def test_active_pack_validators_carry_curator_voice_keys(pack_id: str):
    registry = _registries()[pack_id]
    for entry in registry.validator_metadata:
        if entry.state is not ValidationBindingState.ACTIVE:
            continue
        where = f"{pack_id}:validator:{entry.validator_id}"
        _assert_curator_label(entry.curator_label, where=where)
        _assert_curator_sentence(entry.description, where=f"{where}:description", max_sentences=2)


@pytest.mark.parametrize("pack_id", sorted(_registries()))
def test_attachment_options_expose_when_off_exactly_on_opt_out(pack_id: str):
    registry = _registries()[pack_id]
    for option in registry.validation_attachment_options():
        where = f"{pack_id}:{option.attachment_id}"
        payload = option.to_dict()
        if option.state is ValidationBindingState.ACTIVE:
            assert payload.get("curator_label"), f"{where}: active options expose curator_label"
        else:
            assert "curator_label" not in payload, where
        if option.allow_opt_out:
            assert payload.get("when_off"), f"{where}: opt-out options expose when_off"
        else:
            assert "when_off" not in payload, f"{where}: when_off only on opt-out options"


def test_bindings_and_mirrored_validator_entries_agree():
    """Where a pack lists the same check under validators and validator_bindings,
    the curator sees one sentence, not two."""

    for pack_id, registry in _registries().items():
        entries = {
            entry.validator_id: entry
            for entry in registry.validator_metadata
            if entry.state is ValidationBindingState.ACTIVE
        }
        for binding in registry.bindings:
            if binding.state is not ValidationBindingState.ACTIVE:
                continue
            entry = entries.get(binding.binding_id)
            if entry is None:
                continue
            where = f"{pack_id}:{binding.binding_id}"
            assert entry.curator_label == binding.curator_label, where
            assert entry.description == binding.reason, where
