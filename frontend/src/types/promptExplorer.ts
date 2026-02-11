/**
 * TypeScript types for Prompt Explorer feature.
 * Mirrors the backend Pydantic models.
 */

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
}

// ============================================================================
// Prompt Catalog Types
// ============================================================================

// MOD-specific rule information
export interface MODRuleInfo {
  mod_id: string
  content: string
  source_file: string  // Legacy file path or 'database'
  description?: string

  // Version metadata (from prompt_templates table)
  prompt_id?: string
  prompt_version?: number
  created_at?: string
  created_by?: string
}

// Individual agent prompt information
export interface PromptInfo {
  agent_id: string
  agent_name: string
  description: string
  base_prompt: string
  source_file: string  // Legacy file path or 'database'
  has_mod_rules: boolean
  mod_rules: Record<string, MODRuleInfo>
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
  available_mods: string[]
  last_updated: string
}

// ============================================================================
// Custom Agent Types (Prompt Workshop)
// ============================================================================

export interface CustomAgent {
  id: string
  agent_id: string
  user_id: number
  parent_agent_key: string
  name: string
  description?: string
  custom_prompt: string
  mod_prompt_overrides: Record<string, string>
  icon: string
  include_mod_rules: boolean
  parent_prompt_hash?: string
  current_parent_prompt_hash?: string
  parent_prompt_stale: boolean
  parent_exists: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface CustomAgentVersion {
  id: string
  custom_agent_id: string
  version: number
  custom_prompt: string
  mod_prompt_overrides: Record<string, string>
  notes?: string
  created_at: string
}

export interface PromptPreviewResponse {
  agent_id: string
  prompt: string
  mod_id?: string
  source: 'system_agent' | 'custom_agent'
  parent_agent_key?: string
  include_mod_rules?: boolean
}

export interface CustomAgentTestEvent {
  type: string
  delta?: string
  response?: string
  message?: string
  trace_id?: string
  [key: string]: unknown
}

// Chat message for Opus conversation
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

// Input source type for flow nodes (matches FlowBuilder/types.ts)
export type InputSource = 'user_query' | 'previous_output' | 'custom'

// Flow definition for context (simplified version for chat)
export interface FlowContextDefinition {
  nodes: Array<{
    id: string
    agent_id: string
    agent_display_name: string
    task_instructions?: string  // For task_input nodes
    custom_instructions?: string
    input_source: InputSource
    custom_input?: string
    output_key: string
  }>
  edges: Array<{
    source: string
    target: string
  }>
}

export interface PromptWorkshopContext {
  parent_agent_id?: string
  parent_agent_name?: string
  custom_agent_id?: string
  custom_agent_name?: string
  include_mod_rules?: boolean
  selected_mod_id?: string
  prompt_draft?: string
  selected_mod_prompt_draft?: string
  mod_prompt_override_count?: number
  has_mod_prompt_overrides?: boolean
  parent_prompt_stale?: boolean
  parent_exists?: boolean
}

// Context passed to Opus chat
export interface ChatContext {
  selected_agent_id?: string
  selected_mod_id?: string
  view_mode?: 'base' | 'mod' | 'combined'
  trace_id?: string
  // Flow context (when on Flows tab)
  active_tab?: 'agents' | 'flows' | 'prompt_workshop'  // 'agents' renamed from 'prompts'
  flow_name?: string
  flow_definition?: FlowContextDefinition
  prompt_workshop?: PromptWorkshopContext
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
  mod_applied?: string
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

// SSE event types for Opus chat streaming
export type OpusChatEventType = 'TEXT_DELTA' | 'TOOL_USE' | 'TOOL_RESULT' | 'DONE' | 'ERROR'

// Tool result from suggestion submission
export interface ToolResult {
  success: boolean
  suggestion_id?: string
  message?: string
  error?: string
}

export interface OpusChatEvent {
  type: OpusChatEventType
  delta?: string
  message?: string
  // For TOOL_USE events
  tool_name?: string
  tool_input?: Record<string, unknown>
  // For TOOL_RESULT events
  result?: ToolResult
}

// Suggestion types
export type SuggestionType = 'improvement' | 'bug' | 'clarification' | 'mod_specific' | 'missing_case' | 'general'

// Manual suggestion submission
export interface SuggestionSubmission {
  agent_id?: string  // Optional for general/trace-based feedback
  suggestion_type: SuggestionType
  summary: string
  detailed_reasoning: string
  proposed_change?: string
  mod_id?: string
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
