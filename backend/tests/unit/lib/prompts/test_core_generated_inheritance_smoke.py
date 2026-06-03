"""The slim contract must reach assembled bundles (incl. the rendered string)."""
from src.lib.prompts.assembly import build_agent_core_prompt


def test_core_generated_is_compact_and_present_in_render():
    bundle = build_agent_core_prompt("phenotype_extractor")
    rendered = bundle.render()
    assert "## Generated Runtime Contract" in rendered
    assert "Validators own these fields" in rendered
    # compact: the generated layer is a small fraction of its old ~9K size
    core_generated = next(l for l in bundle.layers if l.kind == "core_generated")
    assert len(core_generated.content) <= 2500
