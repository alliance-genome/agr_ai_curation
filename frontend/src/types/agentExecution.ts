/** Explicit saved output state; null never means generic extraction. */
import type { PromptLayerManifest } from './promptExplorer'

export interface GenericProfilePin {
  profile_id: string
  profile_revision_id: string
  revision: number
  fingerprint: string
}

export interface DomainExtractionRef {
  package_id: string
  agent_id: string
  domain_pack_id: string
}

export interface AgentExecutionReceipt {
  agent_id: string
  agent_key: string
  agent_revision_id: string
  revision: number
  fingerprint: string
  output_contract: AgentOutputContract
}

export type AgentOutputContract =
  | { output_state: 'none'; output_mode?: null; output_schema_key?: null; generic_profile_ref?: null; domain_extraction_ref?: null }
  | { output_state: 'structured_extraction'; output_mode: 'domain'; output_schema_key: string; generic_profile_ref?: null; domain_extraction_ref?: null }
  | { output_state: 'structured_extraction'; output_mode: 'domain'; output_schema_key?: null; generic_profile_ref?: null; domain_extraction_ref: DomainExtractionRef }
  | { output_state: 'structured_extraction'; output_mode: 'profile_bound_generic'; output_schema_key?: null; generic_profile_ref: GenericProfilePin; domain_extraction_ref?: null }
  | { output_state: 'structured_extraction'; output_mode: 'unprofiled_generic'; output_schema_key?: null; generic_profile_ref?: null; domain_extraction_ref?: null }

export interface AgentExecutionSnapshot {
  snapshot_version: 1
  model_id: string
  model_temperature: number
  model_reasoning: string | null
  instructions: string
  instructions_hash: string
  prompt_layer_manifest: PromptLayerManifest
  group_prompt_layers: Record<string, PromptLayerManifest>
  tool_ids: string[]
  system_managed_tool_ids: string[]
  group_tool_policy: Record<string, unknown>
  allowed_group_ids: string[]
  inherited_allowed_group_ids: string[]
  group_rules_enabled: boolean
  group_rules_component: string | null
  group_prompt_overrides: Record<string, string>
  template_source: string | null
  output_contract: AgentOutputContract
  curation: Record<string, unknown> | null
  structured_finalization: Record<string, unknown> | null
}

export interface AgentExecutionRevision {
  id: string
  agent_id: string
  revision: number
  fingerprint: string
  snapshot: AgentExecutionSnapshot
  notes?: string | null
  created_at: string
}

export interface AgentExecutionRevisionPage {
  revisions: AgentExecutionRevision[]
  next_before_revision: number | null
}
