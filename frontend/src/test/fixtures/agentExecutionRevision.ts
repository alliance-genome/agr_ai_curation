import type { AgentExecutionRevision } from '@/types/agentExecution'

export function buildExecutionRevision(revision: number): AgentExecutionRevision {
  return {
    id: `version-${revision}`,
    agent_id: '11111111-1111-1111-1111-111111111111',
    revision,
    fingerprint: 'a'.repeat(64),
    created_at: `2026-02-2${revision}T00:00:00Z`,
    snapshot: {
      snapshot_version: 1,
      model_id: 'gpt-5.6-terra',
      model_temperature: 0.1,
      model_reasoning: null,
      instructions: 'Prompt',
      instructions_hash: 'b'.repeat(64),
      prompt_layer_manifest: { agent_id: 'agent', layers: [], hash: 'b'.repeat(64) },
      group_prompt_layers: {},
      tool_ids: [],
      system_managed_tool_ids: [],
      group_tool_policy: {},
      allowed_group_ids: [],
      inherited_allowed_group_ids: [],
      group_rules_enabled: false,
      group_rules_component: null,
      group_prompt_overrides: {},
      template_source: null,
      output_contract: { output_state: 'none' },
      curation: null,
      structured_finalization: null,
    },
  }
}
