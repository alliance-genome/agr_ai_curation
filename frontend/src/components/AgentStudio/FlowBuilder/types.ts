/**
 * TypeScript types for FlowBuilder components.
 * Mirrors the backend Pydantic schemas for flow definitions.
 */

import type { Node, Edge } from 'reactflow'
import type { Ref } from 'react'
import type { ValidationAttachmentOption } from '@/services/agentStudioService'
import type { AgentBrowserRequest } from '../agentBrowserRequest'
import type { FlowAuthoringProposal } from '@/types/promptExplorer'
import type { AgentExecutionReceipt } from '@/types/agentExecution'

export type { AgentBrowserRequest, AgentBrowserTab, AgentBrowserFocus } from '../agentBrowserRequest'

// ============================================================================
// Agent Catalog Types (from /api/agent-studio/catalog)
// ============================================================================

export interface AgentInfo {
  agent_id: string
  agent_revision_id?: string | null
  agent_name: string
  description: string
  category: string
  subcategory?: string
  has_group_rules: boolean
  tools: string[]
  show_in_palette?: boolean
  prompt_version?: number
}

export interface AgentCategory {
  category: string
  agents: AgentInfo[]
}

// ============================================================================
// Flow Definition Types (matches backend Pydantic schemas)
// ============================================================================

export type NodeType = 'agent' | 'decision' | 'output' | 'task_input'
export type FlowEdgeRole = 'control_flow' | 'output_attachment' | 'validation_attachment'

export interface ValidationAttachmentSelection extends ValidationAttachmentOption {
  enabled: boolean
}

export const validationAttachmentForPersistence = <T extends { export_blocking?: boolean }>(
  attachment: T
): Omit<T, 'export_blocking'> => {
  const { export_blocking: _exportBlocking, ...selection } = attachment
  return selection
}

export interface ValidationAttachmentGroup {
  group_id: string
  state: 'automatic' | 'skipped' | 'replaced' | 'supplemental'
  binding_id?: string | null
  attachment_id?: string | null
  edge_id?: string | null
  validator_node_id?: string | null
  replaces_attachment_id?: string | null
  label?: string | null
  required: boolean
  blocking: boolean
  allow_opt_out: boolean
}

export interface FlowNodePosition {
  x: number
  y: number
}

export interface FlowNodeData {
  agent_id: string
  agent_revision_id?: string | null
  execution_receipt?: AgentExecutionReceipt | null
  agent_display_name: string
  agent_description?: string
  /** Curator's task/request that initiates the flow (required for task_input nodes) */
  task_instructions?: string
  /** Optional human-authored goal for this step, persisted with backend-created flows */
  step_goal?: string
  /** Additional custom instructions appended to agent prompts */
  custom_instructions?: string
  prompt_version?: number
  /** For output/formatter steps only. Defaults to true when omitted. */
  include_evidence?: boolean
  /** For output/formatter steps only. Controls the human-readable output descriptor. */
  output_filename_template?: string
  /** For terminal formatter steps. Backend-validated projection plan for curation exports. */
  export_execution_mode?: 'ai' | 'direct'
  projection_plan?: Record<string, unknown> | null
  output_key: string
  validation_attachments?: ValidationAttachmentSelection[]
  validation_groups?: ValidationAttachmentGroup[]
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
  role?: FlowEdgeRole
  satisfies_binding_id?: string
  replaces_attachment_id?: string
  condition?: FlowEdgeCondition
}

interface FlowDefinitionBody {
  /** Migrated default, omitted whenever the run supplies a user query. */
  task_instructions_default_only?: boolean
  nodes: FlowNodeDefinition[]
  edges: FlowEdgeDefinition[]
  entry_node_id: string
}

/** Current flow schema accepted for editor mutations and save requests. */
export type FlowDefinition = FlowDefinitionBody & { version: '1.1' }

// ============================================================================
// API Response Types
// ============================================================================

export interface FlowResponse {
  id: string
  user_id: number
  name: string
  description: string | null
  // Migration d6e7f8a9b0c1 guarantees persisted flows are v1.1.
  flow_definition: FlowDefinition
  execution_count: number
  last_executed_at: string | null
  created_at: string
  updated_at: string
  validation_warnings?: Array<{
    type: 'CRITICAL' | 'WARNING'
    message: string
  }>
  has_critical_issues?: boolean
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
  /** Derived from output_attachment edges for display only; never persisted. */
  outputBinding?: OutputBindingView
}

/** Persisted step data only; drops UI state such as hasError and outputBinding. */
export const flowNodeDataForPersistence = (data: AgentNodeData): FlowNodeData => {
  const persisted: FlowNodeData = {
    agent_id: data.agent_id,
    agent_display_name: data.agent_display_name,
    output_key: data.output_key,
  }

  if (data.agent_description !== undefined) {
    persisted.agent_description = data.agent_description
  }
  if (data.task_instructions !== undefined) {
    persisted.task_instructions = data.task_instructions
  }
  if (data.step_goal !== undefined) {
    persisted.step_goal = data.step_goal
  }
  if (data.custom_instructions !== undefined) {
    persisted.custom_instructions = data.custom_instructions
  }
  if (data.prompt_version !== undefined) {
    persisted.prompt_version = data.prompt_version
  }
  if (data.agent_revision_id !== undefined) {
    persisted.agent_revision_id = data.agent_revision_id
  }
  if (data.execution_receipt !== undefined) {
    persisted.execution_receipt = data.execution_receipt
  }
  if (data.include_evidence !== undefined) {
    persisted.include_evidence = data.include_evidence
  }
  if (data.output_filename_template !== undefined) {
    persisted.output_filename_template = data.output_filename_template
  }
  if (data.export_execution_mode !== undefined) persisted.export_execution_mode = data.export_execution_mode
  if (data.projection_plan !== undefined) {
    persisted.projection_plan = data.projection_plan
  }
  if (data.validation_attachments !== undefined) {
    persisted.validation_attachments = data.validation_attachments.map(validationAttachmentForPersistence)
  }

  return persisted
}

/** Persisted node type: the input step by agent id, output steps by node type, otherwise agent. */
export const flowNodeTypeForPersistence = (
  node: Pick<AgentNode, 'type' | 'data'>
): FlowNodeDefinition['type'] => (
  node.data.agent_id === 'task_input'
    ? 'task_input'
    : node.type === 'output'
      ? 'output'
      : 'agent'
)

export interface OutputBindingView {
  status: 'bound' | 'missing' | 'duplicate' | 'incompatible'
  sources: Array<{
    sourceNodeId: string
    sourceLabel: string
  }>
  /** Compatibility alias populated only for a single-source binding. */
  sourceNodeId?: string
  /** Compatibility alias populated only for a single-source binding. */
  sourceLabel?: string
}

/** React Flow node with our custom data. */
export type AgentNode = Node<AgentNodeData, 'agent' | 'output' | 'task_input'>

/** React Flow edge with our custom styling */
export type FlowEdge = Edge<{
  animated?: boolean
  isHovered?: boolean
  role?: FlowEdgeRole
  satisfies_binding_id?: string
  replaces_attachment_id?: string
  condition?: FlowEdgeCondition
  validationLabel?: string
  onDeleteEdge?: (edgeId: string) => void
}>

// ============================================================================
// UI Component Props
// ============================================================================

/** Flow state reported to parent for context sharing */
export interface FlowState {
  flowId?: string
  flowName: string
  flowDescription: string
  flowUpdatedAt?: string
  isDirty: boolean
  version: FlowDefinition['version']
  task_instructions_default_only?: boolean
  entry_node_id?: string
  nodes: Array<{
    id: string
    type: NodeType
    position: FlowNodePosition
    agent_id: string
    agent_revision_id?: string | null
    execution_receipt?: AgentExecutionReceipt | null
    agent_display_name: string
    agent_description?: string
    task_instructions?: string
    step_goal?: string
    custom_instructions?: string
    prompt_version?: number
    include_evidence?: boolean
    output_filename_template?: string
    export_execution_mode?: 'ai' | 'direct'
  projection_plan?: Record<string, unknown> | null
    output_key: string
    validation_attachments?: ValidationAttachmentSelection[]
    validation_groups?: ValidationAttachmentGroup[]
  }>
  edges: Array<{
    id: string
    source: string
    target: string
    role?: FlowEdgeRole
    satisfies_binding_id?: string
    replaces_attachment_id?: string
    condition?: FlowEdgeCondition
  }>
}

export interface FlowBuilderProps {
  recoveryOwnerId?: string
  /** Currently editing flow ID (null for new flow) */
  flowId?: string | null
  /** Callback when flow is saved */
  onFlowSaved?: (flowId: string) => void
  /** Callback when flow state changes (for sharing context with chat) */
  onFlowChange?: (flowState: FlowState) => void
  /** Callback to trigger an AI Chat verification request */
  onVerifyRequest?: () => void
  /** Opens the Agent Browser on an agent's Guide, Envelope, or Prompts tab. */
  onOpenAgent?: (request: AgentBrowserRequest) => void
  onOutputHelp?: (agentId: string, agentName: string, prompt: string) => void
  /**
   * False while the Flows tab is hidden. The builder stays mounted so an
   * unsaved graph survives a visit to the Agent Browser, but a hidden builder
   * must not answer keyboard shortcuts. Defaults to true.
   */
  active?: boolean
  /** Synchronous access to the current exact draft for AI Chat submission. */
  authoringContextRef?: Ref<FlowAuthoringContextHandle>
}

export interface FlowAuthoringContextHandle {
  captureAuthoringContext: () => FlowState
  applyAuthoringProposal: (proposal: FlowAuthoringProposal) => Promise<FlowProposalApplyResult>
}

export interface FlowProposalApplyResult {
  applied: boolean
  reason?: 'stale' | 'invalid' | 'unavailable'
  message: string
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
//   const icon = useAgentIcon('gene_validation')  // Returns "🧬"
// ============================================================================

// ============================================================================
// Validation Types
// ============================================================================

export interface ValidationError {
  nodeId?: string
  edgeId?: string
  type: 'disconnected' | 'missing_entry' | 'duplicate_output_key' | 'cycle_detected' | 'missing_task_instructions' | 'multiple_task_inputs'
  message: string
}

export interface ValidationResult {
  isValid: boolean
  errors: ValidationError[]
  warnings: string[]
}
