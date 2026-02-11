/**
 * TypeScript types for FlowBuilder components.
 * Mirrors the backend Pydantic schemas for flow definitions.
 */

import type { Node, Edge } from 'reactflow'

// ============================================================================
// Agent Catalog Types (from /api/agent-studio/catalog)
// ============================================================================

export interface AgentInfo {
  agent_id: string
  agent_name: string
  description: string
  category: string
  subcategory?: string
  has_mod_rules: boolean
  tools: string[]
  show_in_palette?: boolean
}

export interface AgentCategory {
  category: string
  agents: AgentInfo[]
}

// ============================================================================
// Flow Definition Types (matches backend Pydantic schemas)
// ============================================================================

export type InputSource = 'user_query' | 'previous_output' | 'custom'
export type NodeType = 'agent' | 'decision' | 'output' | 'task_input'

export interface FlowNodePosition {
  x: number
  y: number
}

export interface FlowNodeData {
  agent_id: string
  agent_display_name: string
  agent_description?: string
  /** Curator's task/request that initiates the flow (required for task_input nodes) */
  task_instructions?: string
  /** Additional custom instructions appended to agent prompts */
  custom_instructions?: string
  prompt_version?: number
  input_source: InputSource
  custom_input?: string
  output_key: string
}

export interface FlowNodeDefinition {
  id: string
  type: NodeType
  position: FlowNodePosition
  data: FlowNodeData
}

export interface FlowEdgeCondition {
  type: 'contains' | 'not_empty' | 'matches_pattern'
  value?: string
}

export interface FlowEdgeDefinition {
  id: string
  source: string
  target: string
  condition?: FlowEdgeCondition
}

export interface FlowDefinition {
  version: '1.0'
  nodes: FlowNodeDefinition[]
  edges: FlowEdgeDefinition[]
  entry_node_id: string
}

// ============================================================================
// API Response Types
// ============================================================================

export interface FlowResponse {
  id: string
  user_id: number
  name: string
  description: string | null
  flow_definition: FlowDefinition
  execution_count: number
  last_executed_at: string | null
  created_at: string
  updated_at: string
}

export interface FlowSummaryResponse {
  id: string
  user_id: number
  name: string
  description: string | null
  step_count: number
  execution_count: number
  last_executed_at: string | null
  created_at: string
  updated_at: string
}

export interface FlowListResponse {
  flows: FlowSummaryResponse[]
  total: number
  page: number
  page_size: number
}

export interface CreateFlowRequest {
  name: string
  description?: string
  flow_definition: FlowDefinition
}

export interface UpdateFlowRequest {
  name?: string
  description?: string
  flow_definition?: FlowDefinition
}

// ============================================================================
// React Flow Integration Types
// ============================================================================

/** Custom data stored in React Flow nodes */
export interface AgentNodeData extends FlowNodeData {
  // Additional UI state
  isSelected?: boolean
  hasError?: boolean
  errorMessage?: string
}

/** React Flow node with our custom data (handles both agent and task_input node types) */
export type AgentNode = Node<AgentNodeData, 'agent' | 'task_input'>

/** React Flow edge with our custom styling */
export type FlowEdge = Edge<{
  animated?: boolean
  isHovered?: boolean
}>

// ============================================================================
// UI Component Props
// ============================================================================

/** Flow state reported to parent for context sharing */
export interface FlowState {
  flowName: string
  nodes: Array<{
    id: string
    agent_id: string
    agent_display_name: string
    task_instructions?: string
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

export interface FlowBuilderProps {
  /** Currently editing flow ID (null for new flow) */
  flowId?: string | null
  /** Callback when flow is saved */
  onFlowSaved?: (flowId: string) => void
  /** Callback when flow state changes (for sharing context with chat) */
  onFlowChange?: (flowState: FlowState) => void
  /** Callback to trigger a verify request to Claude */
  onVerifyRequest?: () => void
}

export interface AgentPaletteProps {
  /** Whether the palette is collapsed */
  isCollapsed?: boolean
  /** Toggle collapse state */
  onToggleCollapse?: () => void
}

export interface FlowNodeProps {
  data: AgentNodeData
  id: string
  selected: boolean
}

export interface NodeEditorProps {
  /** The node being edited */
  node: AgentNode | null
  /** Callback to save changes */
  onSave: (nodeId: string, data: Partial<AgentNodeData>) => void
  /** Callback to close the editor */
  onClose: () => void
  /** Callback to delete the node */
  onDelete?: (nodeId: string) => void
  /** Available output keys from other nodes (for input templates) */
  availableVariables: string[]
  /** Callback to view the agent's prompts */
  onViewPrompts?: (agentId: string, agentName: string) => void
  /** Whether this node has an incoming edge (for enabling "Previous Step Output") */
  hasIncomingEdge?: boolean
  /** Callback to mark node as manually configured when user saves in NodeEditor */
  onMarkManuallyConfigured?: (nodeId: string) => void
}

// ============================================================================
// Agent Icon - Use hooks from @/hooks/useAgentIcon
// ============================================================================
// Icons are now fetched from the registry API via AgentMetadataContext.
// Use the following hooks instead of hardcoded icon mappings:
//   - useAgentIcon(agentId) - Get icon for a single agent
//   - useAgentMetadata() - Get all agent metadata including icons
//
// Example:
//   import { useAgentIcon } from '@/hooks/useAgentIcon'
//   const icon = useAgentIcon('gene')  // Returns "🧬"
// ============================================================================

// ============================================================================
// Validation Types
// ============================================================================

export interface ValidationError {
  nodeId?: string
  edgeId?: string
  type: 'disconnected' | 'missing_entry' | 'duplicate_output_key' | 'invalid_input_source' | 'cycle_detected' | 'missing_task_instructions' | 'multiple_task_inputs'
  message: string
}

export interface ValidationResult {
  isValid: boolean
  errors: ValidationError[]
  warnings: string[]
}
