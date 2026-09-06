/**
 * TypeScript types for Prompt Explorer feature.
 * Mirrors the backend Pydantic models.
 */

import type { AgentExecutionReceipt } from './agentExecution'
import type { WorkshopOutputDraft } from '@/components/AgentStudio/PromptWorkshop/workshopOutputDraft'

// ============================================================================
// Agent Documentation Types
// ============================================================================

// A single capability of an agent with optional example
export interface AgentCapability {
  name: string
  description: string
  example_query?: string
  example_result?: string
}

// Information about a data source an agent can access
export interface DataSourceInfo {
  name: string
  description: string
  species_supported?: string[]
  data_types?: string[]
}

// Curator-friendly documentation for an agent
export interface AgentDocumentation {
  summary: string
  capabilities: AgentCapability[]
  data_sources: DataSourceInfo[]
  limitations: string[]
  // Curator-voice guidance: when this agent is the right choice
  use_when: string[]
  // Curator-voice guidance: when another agent is the right choice
  avoid_when: string[]
  // Curator-voice note shown above the guidance, verbatim from docs.yaml; empty when none
  note: string
}

// ============================================================================
// Prompt Catalog Types
// ============================================================================

// Group-specific rule information
export interface GroupRuleInfo {
  group_id: string
  content: string
  source_file: string  // Legacy file path or 'database'
  description?: string

  // Version metadata (from prompt_templates table)
  prompt_id?: string
  prompt_version?: number
  created_at?: string
  created_by?: string
}

export type PromptLayerKind =
  | 'core_static'
  | 'core_generated'
  | 'base_prompt'
  | 'group_rules'
  | 'curator_overlay'
  | 'runtime_context'

export interface PromptLayerInfo {
  id: string
  kind: PromptLayerKind
  title: string
  content: string
  provenance: string
  editable: boolean
  locked: boolean
  source_ref: string
  hash: string
}

export interface PromptLayerManifest {
  agent_id: string
  layers: PromptLayerInfo[]
  hash: string
}

export interface CombinedPromptResponse {
  agent_id: string
  group_id: string
  combined_prompt: string
  effective_prompt_hash: string
  layer_manifest: PromptLayerManifest
}

// Individual agent prompt information
export interface PromptInfo {
  agent_revision_id?: string | null
  agent_id: string
  agent_name: string
  description: string
  base_prompt: string
  source_file: string  // Legacy file path or 'database'
  has_group_rules: boolean
  group_rules: Record<string, GroupRuleInfo>
  prompt_layers?: PromptLayerInfo[]
  effective_prompt_hash?: string
  layer_manifest?: Record<string, unknown>
  prompt_layer_error?: string
  custom_prompt_overlay_status?: 'clean' | 'deduplicated' | 'needs_review'
  custom_prompt_removed_layer_kinds?: string[]
  custom_prompt_warning?: string
  tools: string[]
  model?: string
  subcategory?: string  // Subcategory for palette grouping
  show_in_palette?: boolean  // Whether agent appears in Flow Builder palette (default true)

  // Curator-friendly documentation
  documentation?: AgentDocumentation

  // Version metadata (from prompt_templates table)
  prompt_id?: string
  prompt_version?: number
  created_at?: string
  created_by?: string
}

// Agents grouped by category
export interface AgentPrompts {
  category: string
  agents: PromptInfo[]
}

// Full prompt catalog
export interface PromptCatalog {
  categories: AgentPrompts[]
  total_agents: number
  available_groups: string[]
  last_updated: string
}

// ============================================================================
// Agent Workshop Model + Tool Library Types
// ============================================================================

export interface ModelOption {
  model_id: string
  name: string
  provider: string
  description: string
  guidance: string
  default: boolean
  supports_reasoning: boolean
  supports_temperature: boolean
  reasoning_options: string[]
  default_reasoning?: string
  reasoning_descriptions: Record<string, string>
  recommended_for: string[]
  avoid_for: string[]
}

/** Tool policy config; `requires_document` is derived by the backend from the tool registry. */
export interface ToolLibraryConfig {
  requires_document: boolean
  [key: string]: unknown
}

export interface ToolLibraryItem {
  tool_key: string
  display_name: string
  description: string
  category: string
  curator_visible: boolean
  allow_attach: boolean
  allow_execute: boolean
  config: ToolLibraryConfig
}

export interface AgentTemplate {
  agent_id: string
  name: string
  description?: string
  icon: string
  category?: string
  model_id: string
  tool_ids: string[]
  allowed_group_ids: string[]
  output_schema_key?: string
  output_contract?: import('./agentExecution').AgentOutputContract
}

export interface GroupOption {
  group_id: string
  name: string
}

export type ToolIdeaStatus = 'submitted' | 'reviewed' | 'in_progress' | 'completed' | 'declined'

export interface ToolIdeaConversationEntry {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string | null
}

export interface ToolIdeaRequest {
  id: string
  user_id: number
  project_id?: string
  title: string
  description: string
  opus_conversation: ToolIdeaConversationEntry[]
  status: ToolIdeaStatus
  developer_notes?: string
  resulting_tool_key?: string
  created_at: string
  updated_at: string
}

// ============================================================================
// Custom Agent Types (Agent Workshop)
// ============================================================================

export interface CustomAgent {
  id: string
  agent_id: string
  execution_revision_id?: string | null
  user_id: number
  template_source?: string
  name: string
  description?: string
  custom_prompt: string
  custom_prompt_overlay_status?: 'clean' | 'deduplicated' | 'needs_review'
  custom_prompt_removed_layer_kinds?: string[]
  custom_prompt_warning?: string
  group_prompt_overrides: Record<string, string>
  allowed_group_ids: string[]
  inherited_allowed_group_ids: string[]
  icon: string
  include_group_rules: boolean
  model_id: string
  model_temperature: number
  model_reasoning?: string
  tool_ids: string[]
  output_schema_key?: string
  visibility: string
  project_id?: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface CustomAgentVersion {
  /** Historical prompt-only audit record; never runnable or restorable. */
  executable: false
  id: string
  custom_agent_id: string
  version: number
  custom_prompt: string
  group_prompt_overrides: Record<string, string>
  allowed_group_ids: string[]
  notes?: string
  created_at: string
}

export interface PromptPreviewResponse {
  agent_id: string
  prompt: string
  group_id?: string
  source: 'system_agent' | 'custom_agent'
  parent_agent_key?: string
  include_group_rules?: boolean
  effective_prompt_hash?: string
  layer_manifest?: {
    agent_id: string
    layers: PromptLayerInfo[]
    hash: string
  }
}

export interface CustomAgentTestEvent {
  type: string
  delta?: string
  response?: string
  message?: string
  trace_id?: string
  [key: string]: unknown
}

// Chat message for the Agent Studio AI Chat conversation
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

// Lossless save-equivalent Flow Builder draft passed to AI Chat.
export interface FlowContextDefinition {
  version: '1.1'
  task_instructions_default_only?: boolean
  entry_node_id?: string
  nodes: Array<{
    id: string
    node_type: 'agent' | 'decision' | 'output' | 'task_input'
    position: { x: number; y: number }
    agent_id: string
    agent_revision_id?: string | null
    execution_receipt?: AgentExecutionReceipt | null
    agent_display_name: string
    agent_description?: string
    task_instructions?: string  // For task_input nodes
    step_goal?: string
    custom_instructions?: string
    prompt_version?: number
    include_evidence?: boolean
    output_filename_template?: string
    projection_plan?: Record<string, unknown>
    output_key: string
    validation_attachments?: Array<Record<string, unknown>>
    validation_groups?: Array<Record<string, unknown>>
  }>
  edges: Array<{
    id: string
    source: string
    target: string
    role?: 'control_flow' | 'output_attachment' | 'validation_attachment'
    satisfies_binding_id?: string
    replaces_attachment_id?: string
    condition?: {
      type: 'contains' | 'not_empty' | 'matches_pattern'
      value?: string
    }
  }>
}

export interface AgentWorkshopContext {
  getting_started_mode?: 'template' | 'scratch' | 'clone'
  template_source?: string
  clone_source_agent_id?: string
  clone_source_updated_at?: string
  template_name?: string
  custom_agent_id?: string
  custom_agent_name?: string
  draft_name?: string
  draft_description?: string
  draft_icon?: string
  draft_visibility?: 'private' | 'project'
  draft_allowed_group_ids?: string[]
  inherited_allowed_group_ids?: string[]
  include_group_rules?: boolean
  selected_group_id?: string
  prompt_draft?: string
  selected_group_prompt_draft?: string
  group_prompt_overrides?: Record<string, string>
  draft_is_dirty?: boolean
  draft_fingerprint?: string
  custom_agent_updated_at?: string
  group_prompt_override_count?: number
  has_group_prompt_overrides?: boolean
  draft_tool_ids?: string[]
  draft_model_id?: string
  draft_model_reasoning?: string
  draft_output_schema_key?: string
  /** Complete local structure, including incomplete unsaved input; never an execution receipt. */
  draft_output?: WorkshopOutputDraft
}

export interface WorkshopAuthoringProposal {
  assumptions?: string[]
  contract_version: 'workshop_authoring_proposal.v1'
  base_draft_fingerprint: string
  candidate_draft_fingerprint: string
  candidate: AgentWorkshopContext
  change_summary: string
  diff: FlowAuthoringDiffEntry[]
  findings: FlowAuthoringFinding[]
}

export interface WorkshopContinuationOrigin {
  flow_id?: string
  flow_draft_fingerprint: string
  node_id?: string
  agent_id?: string
  agent_revision_id?: string | null
}

export interface WorkshopActionRequest {
  action: 'open_agent' | 'new_agent' | 'save' | 'save_as' | 'show_section' | 'return_to_flow'
  agent_id?: string
  node_id?: string
  mode?: 'scratch' | 'template' | 'clone'
  section?: 'setup' | 'output_structure' | 'prompt' | 'tools' | 'versions' | 'tool_request' | 'manage'
}

export interface WorkshopAction {
  success: true
  contract_version: 'workshop_action.v1'
  request: WorkshopActionRequest
  label: string
  source: { agent_id: string; name: string; updated_at: string; agent_revision_id: string | null } | null
  origin: WorkshopContinuationOrigin | null
  active_tab: string
  flow_draft_fingerprint: string | null
  workshop_draft_fingerprint: string | null
  saved: false
  message: string
}

export interface WorkshopSavedHandoff {
  status: 'ready' | 'stale_origin' | 'catalog_unavailable'
  saved_agent_id?: string
  saved_custom_agent_id?: string
  saved_agent_revision_id?: string
  saved_agent_name?: string
  origin?: WorkshopContinuationOrigin
}

export interface FlowAuthoringFinding {
  code: string
  severity: 'error' | 'warning' | 'info'
  path: string
  message: string
  fix_hint?: string
  node_id?: string
  edge_id?: string
}

export interface FlowAuthoringDiffEntry {
  kind: 'added' | 'removed' | 'changed'
  path: string
  before?: unknown
  after?: unknown
}

export interface FlowAuthoringProposal {
  contract_version: 'flow_authoring_proposal.v1'
  base_draft_fingerprint: string
  candidate_draft_fingerprint: string
  change_summary: string
  diff: FlowAuthoringDiffEntry[]
  findings: FlowAuthoringFinding[]
  candidate: {
    name: string
    description: string
    flow_definition: import('@/components/AgentStudio/FlowBuilder/types').FlowDefinition
  }
}

// Context passed to Agent Studio AI Chat
export interface ChatContext {
  selected_agent_id?: string
  selected_group_id?: string
  view_mode?: 'base' | 'group' | 'combined'
  trace_id?: string
  session_id?: string
  // Flow context (when on Flows tab)
  active_tab?: 'agents' | 'flows' | 'agent_workshop'
  flow_id?: string
  flow_name?: string
  flow_description?: string
  flow_updated_at?: string
  flow_is_dirty?: boolean
  flow_draft_fingerprint?: string
  flow_definition?: FlowContextDefinition
  agent_workshop?: AgentWorkshopContext
}

// Tool call information from trace
export interface ToolCallInfo {
  name: string
  input: Record<string, unknown>
  output_preview?: string
  duration_ms?: number
  status: string
}

// Routing decision from supervisor
export interface RoutingDecision {
  from_agent: string
  to_agent: string
  reason?: string
  timestamp?: string
}

// Prompt execution in a trace
export interface PromptExecution {
  agent_id: string
  agent_name: string
  prompt_preview: string
  group_applied?: string
  model?: string
  tokens_used?: number
}

// Full trace context for display
export interface TraceContext {
  trace_id: string
  session_id?: string
  timestamp: string
  user_query: string
  final_response_preview: string
  prompts_executed: PromptExecution[]
  routing_decisions: RoutingDecision[]
  tool_calls: ToolCallInfo[]
  total_duration_ms?: number
  total_tokens?: number
  agent_count: number
}

// Provider-neutral SSE contract for Agent Studio AI Chat. The legacy Opus type
// names remain internal compatibility identifiers until the broader cleanup.
export const AGENT_STUDIO_CHAT_EVENT_TYPES = [
  'TEXT_DELTA',
  'TOOL_SEARCH',
  'TOOL_SEARCH_RESULT',
  'TOOL_USE',
  'TOOL_RESULT',
  'PROVIDER_CONTEXT_PREFLIGHT',
  'CONTEXT_OVERFLOW',
  'REFUSAL',
  'INCOMPLETE',
  'DONE',
  'ERROR',
] as const

export type OpusChatEventType = typeof AGENT_STUDIO_CHAT_EVENT_TYPES[number]

// Tool result from suggestion submission
export interface ToolResult {
  success?: boolean
  suggestion_id?: string
  message?: string
  error?: string
  pending_user_approval?: boolean
  apply_mode?: 'replace' | 'targeted_edit'
  proposed_prompt?: string
  target_prompt?: 'main' | 'group'
  target_group_id?: string
  change_summary?: string
  applied_edits?: string[]
  [key: string]: unknown
}

interface AgentStudioChatEventBase {
  session_id: string
  turn_id: string
  trace_id?: string | null
}

export type OpusChatEvent = AgentStudioChatEventBase & (
  | { type: 'TEXT_DELTA'; delta: string }
  | { type: 'TOOL_SEARCH'; status: string; search_id?: string | null }
  | {
      type: 'TOOL_SEARCH_RESULT'
      status: string
      loaded_tool_count: number
      search_id?: string | null
    }
  | {
      type: 'TOOL_USE'
      tool_name: string
      tool_input: Record<string, unknown>
      call_id?: string | null
    }
  | {
      type: 'TOOL_RESULT'
      tool_name: string
      result: ToolResult
      call_id?: string | null
    }
  | {
      type: 'PROVIDER_CONTEXT_PREFLIGHT'
      operation?: string
      provider?: string
      model?: string
      model_live?: boolean
      payload_summary?: Record<string, unknown>
    }
  | {
      type: 'CONTEXT_OVERFLOW' | 'REFUSAL' | 'INCOMPLETE' | 'ERROR'
      message: string
      error_source?: string
    }
  | { type: 'DONE' }
)

// Suggestion types
export type SuggestionType = 'improvement' | 'bug' | 'clarification' | 'group_specific' | 'missing_case' | 'general'

// Manual suggestion submission
export interface SuggestionSubmission {
  agent_id?: string  // Optional for general/trace-based feedback
  suggestion_type: SuggestionType
  summary: string
  detailed_reasoning: string
  proposed_change?: string
  group_id?: string
  trace_id?: string
}

export interface SuggestionResponse {
  status: string
  suggestion_id: string
  message: string
}

// ============================================================================
// Tool Details Types
// ============================================================================

// Parameter definition for a tool
export interface ToolParameter {
  name: string
  type: string
  required: boolean
  description: string
}

// Method definition for multi-method tools like agr_curation_query
export interface ToolMethod {
  name: string
  description: string
  required_params: string[]
  optional_params: string[]
  example: Record<string, unknown>
}

// Agent-specific method context for multi-method tools
export interface AgentMethodContext {
  agent_name: string
  methods: string[]
  description: string
}

// Full tool information
export interface ToolInfo {
  name: string
  description: string
  category: string
  source_file: string
  documentation: {
    summary: string
    parameters: ToolParameter[]
  }
  // For multi-method tools (like agr_curation_query)
  methods?: Record<string, ToolMethod>
  // Maps agent_id prefixes to their relevant methods
  agent_methods?: Record<string, AgentMethodContext>
  // When fetched with agent_id parameter, includes agent-specific context
  agent_context?: AgentMethodContext
  // Subset of methods relevant to the specific agent
  relevant_methods?: Record<string, ToolMethod>
  // For method-level tools: reference to parent tool (e.g., 'agr_curation_query')
  parent_tool?: string
  // Example usage (for method-level tools)
  example?: Record<string, unknown>
}
