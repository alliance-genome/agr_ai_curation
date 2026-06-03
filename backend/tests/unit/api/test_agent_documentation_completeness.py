"""Every curator-facing agent must have non-trivial documentation. This fails CI
when a new agent is added without a docs.yaml (or with an empty one)."""
import pytest

from src.lib.config.agent_loader import load_agent_definitions

# Synthetic flow nodes are documented via system_agent_docs.yaml, not a bundle.
_SYNTHETIC = {"task_input", "curation_prep"}


def _palette_agents():
    return {
        aid: a
        for aid, a in load_agent_definitions().items()
        if a.frontend.show_in_palette and aid not in _SYNTHETIC
    }


@pytest.mark.parametrize("agent_id", sorted(_palette_agents().keys()))
def test_palette_agent_has_nonempty_documentation(agent_id):
    agent = _palette_agents()[agent_id]
    doc = agent.documentation
    assert doc, f"{agent_id}: missing docs.yaml (no documentation loaded)"
    summary = (doc.get("summary") or "").strip()
    assert len(summary.split()) >= 3, f"{agent_id}: summary too short / empty"
    caps = doc.get("capabilities") or []
    assert len(caps) >= 1, f"{agent_id}: needs at least one capability"
    for cap in caps:
        assert (cap.get("name") or "").strip(), f"{agent_id}: capability missing name"
        assert (cap.get("description") or "").strip(), f"{agent_id}: capability missing description"


def test_synthetic_nodes_documented():
    from src.lib.agent_studio.system_agent_docs import get_system_agent_documentation
    for node in _SYNTHETIC:
        doc = get_system_agent_documentation(node)
        assert doc and (doc.get("summary") or "").strip(), f"{node}: missing system doc"
