"""Record the actual application-owned phase prompt and tools at execution setup."""
from src.lib.prompts.assembly import _bundle, _make_layer, prompt_templates_for_bundle
from src.lib.prompts.context import bind_prompt_run, set_pending_prompts
from src.lib.openai_agents.langfuse_client import log_agent_config


def record_phase(agent, *, phase, original_instructions, metadata):
    bundle = _bundle(phase, [
        _make_layer(layer_id=phase + ':configured', kind='runtime_context', title='Configured agent instructions',
            content=original_instructions, provenance='configured_agent', editable=False, locked=True,
            source_ref='saved_executable_agent'),
        _make_layer(layer_id=phase + ':protocol', kind='runtime_context', title='Application-owned phase protocol',
            content=agent.instructions[len(original_instructions):].strip(), provenance='backend:validation_review', editable=False, locked=True,
            source_ref='src.lib.validation_review.protocol'),
    ])
    prompt_id = set_pending_prompts(agent.name, list(prompt_templates_for_bundle(bundle)),
        effective_prompt_hash=bundle.hash, layer_manifest=bundle.to_manifest())
    bind_prompt_run(agent, prompt_id)
    log_agent_config(agent_name=agent.name, instructions=agent.instructions, model=str(agent.model),
        tools=[tool.name for tool in agent.tools], model_settings=agent.model_settings.to_json_dict(),
        metadata={**metadata, 'review_phase': phase, 'effective_prompt_hash': bundle.hash,
                  'layer_manifest': bundle.to_manifest()})
