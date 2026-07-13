import re
from types import SimpleNamespace

from src.lib.agent_studio.models import ChatContext
from src.lib.agent_studio.prompt_builder import build_opus_system_prompt
from src.lib.prompts.assembly import PromptLayer, PromptLayerBundle, PromptLayerKind


def _layer(
    layer_id: str,
    kind: PromptLayerKind,
    title: str,
    content: str,
    *,
    editable: bool,
    locked: bool,
) -> PromptLayer:
    return PromptLayer(
        id=layer_id,
        kind=kind,
        title=title,
        content=content,
        provenance=f"fixture:{kind}",
        editable=editable,
        locked=locked,
        source_ref=f"fixture:{layer_id}",
        hash=f"hash:{layer_id}",
    )


def test_selected_agent_context_uses_canonical_group_prompt_layers_in_runtime_order():
    layers = (
        _layer("gene:core", "core_static", "Core contract", "LOCKED CORE", editable=False, locked=True),
        _layer(
            "gene:generated",
            "core_generated",
            "Generated contract",
            "GENERATED GUIDANCE",
            editable=False,
            locked=True,
        ),
        _layer("gene:base", "base_prompt", "Base prompt", "EDITABLE BASE", editable=True, locked=False),
        _layer(
            "gene:group:group-alpha",
            "group_rules",
            "Group alpha rules",
            "GROUP ALPHA RULES",
            editable=True,
            locked=False,
        ),
    )
    bundle = PromptLayerBundle(agent_id="gene", layers=layers, hash="bundle-hash")
    agent = SimpleNamespace(
        agent_id="gene",
        agent_name="Gene Agent",
        description="Curates genes.",
        tools=["gene_lookup"],
        has_group_rules=True,
        group_rules={"group-alpha": SimpleNamespace(content="LEGACY GROUP ONLY")},
        base_prompt="LEGACY BASE ONLY",
    )
    service = SimpleNamespace(
        get_agent=lambda agent_id: agent if agent_id == "gene" else None,
        get_effective_prompt_bundle=lambda agent_id, *, group_id=None: bundle,
    )

    prompt = build_opus_system_prompt(
        ChatContext(selected_agent_id="gene", selected_group_id="group-alpha"),
        load_template=lambda: "{{USER_GREETING}}\n{{PACKAGE_DIAGNOSTIC_TOOLS}}",
        list_model_definitions=lambda: [],
        get_prompt_catalog=lambda: service,
        prepare_trace_context=lambda _trace_id: None,
    )

    assert 'selected_group="group-alpha"' in prompt
    assert 'kind="core_static" editable="false" locked="true"' in prompt
    assert 'kind="base_prompt" editable="true" locked="false"' in prompt
    assert prompt.index("LOCKED CORE") < prompt.index("GENERATED GUIDANCE")
    assert prompt.index("GENERATED GUIDANCE") < prompt.index("EDITABLE BASE")
    assert prompt.index("EDITABLE BASE") < prompt.index("GROUP ALPHA RULES")
    assert prompt.count("LOCKED CORE") == 1
    assert prompt.count("GENERATED GUIDANCE") == 1
    assert prompt.count("EDITABLE BASE") == 1
    assert prompt.count("GROUP ALPHA RULES") == 1

    combined_match = re.search(
        r'<combined_prompt agent="gene" selected_group="group-alpha">\n(.*?)\n</combined_prompt>',
        prompt,
        re.DOTALL,
    )
    assert combined_match is not None
    combined_prompt = combined_match.group(1)
    assert combined_prompt == bundle.render()

    expected_offset = 0
    for layer in layers:
        layer_match = re.search(
            rf'<prompt_layer order="\d+" kind="{layer.kind}"[^>]* '
            rf'content_start="(\d+)" content_end="(\d+)">',
            prompt,
        )
        assert layer_match is not None
        content_start, content_end = map(int, layer_match.groups())
        assert content_start == expected_offset
        assert combined_prompt[content_start:content_end] == layer.content
        expected_offset = content_end + len("\n\n")

    assert "LEGACY BASE ONLY" not in prompt
    assert "LEGACY GROUP ONLY" not in prompt
