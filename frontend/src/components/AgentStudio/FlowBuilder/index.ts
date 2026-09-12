/**
 * FlowBuilder Module Exports
 *
 * Visual node-based flow editor for creating curation flows.
 */

export { default as FlowBuilder } from './FlowBuilder'
export { default as FlowNode } from './FlowNode'
export { default as AgentPalette } from './AgentPalette'
export { NodePanel, NodePanelDock } from './NodePanel'

// Re-export types
export type {
  // Agent catalog types
  AgentInfo,
  AgentCategory,
  // Flow definition types
  NodeType,
  FlowNodePosition,
  FlowNodeData,
  FlowNodeDefinition,
  FlowEdgeCondition,
  FlowEdgeDefinition,
  FlowDefinition,
  // API response types
  FlowResponse,
  FlowSummaryResponse,
  FlowListResponse,
  CreateFlowRequest,
  UpdateFlowRequest,
  // React Flow types
  AgentNodeData,
  AgentNode,
  FlowEdge,
  // Component props
  FlowBuilderProps,
  FlowState,
  FlowAuthoringContextHandle,
  AgentPaletteProps,
  FlowNodeProps,
  AgentBrowserRequest,
  // Validation types
  ValidationError,
  ValidationResult,
} from './types'

// Note: Agent icons are now fetched from the registry API.
// Use the useAgentIcon hook from '@/hooks/useAgentIcon' instead.
