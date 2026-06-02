"""The per-agent documentation served by the catalog must not change during the
faithful port. Regenerate the baseline intentionally (DELETE the json + rerun)
only when authored content legitimately changes (later task)."""
import json
from pathlib import Path

from src.lib.agent_studio.registry_builder import build_agent_registry
from src.lib.agent_studio.catalog_service import _convert_documentation

BASELINE = Path(__file__).parent / "fixtures" / "agent_documentation_baseline.json"


def _current_documentation_snapshot() -> dict:
    registry = build_agent_registry()
    snapshot = {}
    for agent_id, entry in registry.items():
        doc = _convert_documentation(entry.get("documentation"))
        snapshot[agent_id] = doc.model_dump() if doc is not None else None
    return snapshot


def test_agent_documentation_matches_baseline():
    snapshot = _current_documentation_snapshot()
    if not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        # First run writes the baseline; rerun asserts against it.
        return
    expected = json.loads(BASELINE.read_text())
    assert snapshot == expected, (
        "Per-agent documentation changed. If this is an intentional authored "
        "change (later task), delete the baseline json and rerun to regenerate."
    )
