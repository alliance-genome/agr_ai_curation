"""The tool catalog Agent Studio serves must not change during the Phase 2
faithful migration (tool docs -> bindings.yaml). Regenerate the baseline
intentionally (DELETE the json + rerun) only when the served tool docs
legitimately change (the transfer_to_* removal and the reviewed voice pass)."""
import json
from pathlib import Path

from src.lib.agent_studio.catalog_service import get_all_tools

BASELINE = Path(__file__).parent / "fixtures" / "tool_catalog_baseline.json"


def _current_tool_snapshot() -> dict:
    # Normalize through JSON (default=str) so non-serializable parameter defaults
    # compare deterministically and match the committed fixture's shape.
    return json.loads(json.dumps(get_all_tools(), sort_keys=True, default=str))


def test_tool_catalog_matches_baseline():
    snapshot = _current_tool_snapshot()
    if not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        # First run writes the baseline; rerun asserts against it.
        return
    expected = json.loads(BASELINE.read_text())
    assert snapshot == expected, (
        "Tool catalog changed. If this is an intentional change (transfer_to_* "
        "removal or reviewed voice pass), delete the baseline json and rerun to "
        "regenerate, then review the diff."
    )
