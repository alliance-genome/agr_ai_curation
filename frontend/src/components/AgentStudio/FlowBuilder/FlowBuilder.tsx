/**
 * FlowBuilder Component
 *
 * Main flow canvas using React Flow for visual flow editing.
 * Supports drag-drop from palette, node editing, and save/load.
 */

import { useState, useCallback, useRef, useMemo, useEffect } from 'react'
import ReactFlow, {
  ReactFlowProvider,
  Controls,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  addEdge,
  type Connection,
  type ReactFlowInstance,
} from 'reactflow'
import 'reactflow/dist/style.css'
import {
  Box,
  Typography,
  TextField,
  Paper,
  Snackbar,
  Alert,
  CircularProgress,
  Menu,
  MenuItem,
  ListItemText,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemButton,
  InputAdornment,
  IconButton,
  Tooltip,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import SearchIcon from '@mui/icons-material/Search'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh'
import NoteAddIcon from '@mui/icons-material/NoteAdd'
import FolderOpenIcon from '@mui/icons-material/FolderOpen'
import ListAltIcon from '@mui/icons-material/ListAlt'
import SaveIcon from '@mui/icons-material/Save'
import SaveAsIcon from '@mui/icons-material/SaveAs'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'

import FlowNode from './FlowNode'
import DeletableEdge from './DeletableEdge'
import AgentPalette from './AgentPalette'
import NodeEditor from './NodeEditor'
import TaskInputEditor from './TaskInputEditor'
import PromptViewer from './PromptViewer'
import DomainEnvelopeViewer from './DomainEnvelopeViewer'
import { validationAttachmentForPersistence } from './types'
import { projectExecutableFlowGraph } from './executableFlowGraph'
import type {
  FlowBuilderProps,
  FlowState,
  AgentNode,
  AgentNodeData,
  FlowDefinition,
  FlowNodeData,
  FlowResponse,
  FlowEdge,
  FlowEdgeRole,
  NodeType,
  OutputBindingView,
  ValidationAttachmentGroup,
  ValidationAttachmentSelection,
} from './types'
import {
  getFlow,
  createFlow,
  updateFlow,
  listFlows,
  deleteFlow,
} from '@/services/agentStudioService'
import type { FlowSummaryResponse } from './types'
import logger from '@/services/logger'
import { notifyFlowListInvalidated } from '@/features/flows/flowListInvalidation'
import {
  canSourceOutputAttachmentFromMetadata,
  resolveOutputFormatterIncludeEvidence,
  isOutputFormatterAgentFromMetadata,
  isValidationAgentFromMetadata,
} from './agentMetadataUtils'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'

/**
 * Helper to create initial task_input node for new flows.
 * Extracted to avoid duplication between mount useEffect and handleNewFlow.
 */
const createInitialTaskInputNode = (): AgentNode => ({
  id: 'node_0',
  type: 'task_input',
  position: { x: 250, y: 100 },
  data: {
    agent_id: 'task_input',
    agent_display_name: 'Initial Instructions',
    agent_description: 'Define the task for this flow',
    task_instructions: '',
    custom_instructions: '',
    output_key: 'task_input',
  },
})

const buildDefaultValidationSelections = (
  agentId: string,
  agentMetadata: ReturnType<typeof useAgentMetadata>['agents']
): ValidationAttachmentSelection[] => (
  agentMetadata[agentId]?.validation_attachments?.map((attachment) => ({
    ...validationAttachmentForPersistence(attachment),
    enabled: attachment.default_enabled,
  })) ?? []
)

const flowNodeDataForPersistence = (data: AgentNodeData): FlowNodeData => {
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
  if (data.include_evidence !== undefined) {
    persisted.include_evidence = data.include_evidence
  }
  if (data.output_filename_template !== undefined) {
    persisted.output_filename_template = data.output_filename_template
  }
  if (data.projection_plan !== undefined) {
    persisted.projection_plan = data.projection_plan
  }
  if (data.validation_attachments !== undefined) {
    persisted.validation_attachments = data.validation_attachments.map(validationAttachmentForPersistence)
  }

  return persisted
}

const activeValidationBindingOptions = (
  node?: AgentNode
): ValidationAttachmentSelection[] => (
  node?.data.validation_attachments?.filter((attachment) => (
    attachment.state === 'active'
    && Boolean(attachment.validator_binding_id)
    && attachment.enabled === true
  )) ?? []
)

const edgeRole = (edge: FlowEdge): FlowEdgeRole =>
  edge.data?.role ?? 'control_flow'

const validationEdgeLabel = (binding: ValidationAttachmentSelection): string =>
  binding.target_label || binding.label

const nextValidationEdgeId = (existingEdges: FlowEdge[]): string => {
  let index = existingEdges.length + 1
  let candidate = `validation_${index}`
  const existingIds = new Set(existingEdges.map((edge) => edge.id))
  while (existingIds.has(candidate)) {
    index += 1
    candidate = `validation_${index}`
  }
  return candidate
}

const nextOutputEdgeId = (existingEdges: FlowEdge[]): string => {
  let index = existingEdges.length + 1
  let candidate = `output_${index}`
  const existingIds = new Set(existingEdges.map((edge) => edge.id))
  while (existingIds.has(candidate)) {
    index += 1
    candidate = `output_${index}`
  }
  return candidate
}

const unsupportedFlowVersionMessage = (
  flowName: string,
  version: string,
): string => (
  `Flow '${flowName}' uses unsupported schema version '${version}'. `
  + 'Upgrade or archive it before editing.'
)

export const rebuildValidationGroupsFromEdges = (
  currentNodes: AgentNode[],
  currentEdges: FlowEdge[]
): AgentNode[] => {
  let changed = false

  const nextNodes = currentNodes.map((node) => {
    const replacementsByBinding = new Map<string, { edgeId: string; target: string }>()
    currentEdges.forEach((edge) => {
      if (edgeRole(edge) !== 'validation_attachment' || edge.source !== node.id) return
      const bindingId = edge.data?.satisfies_binding_id
      if (!bindingId) return
      replacementsByBinding.set(bindingId, {
        edgeId: edge.id,
        target: edge.target,
      })
    })

    const validationAttachments = node.data.validation_attachments ?? []
    const existingGroups = node.data.validation_groups ?? []

    if (
      validationAttachments.length === 0
      && existingGroups.length === 0
      && replacementsByBinding.size === 0
    ) {
      return node
    }

    const declaredBindingIds = new Set(
      validationAttachments
        .map((attachment) => attachment.validator_binding_id)
        .filter((bindingId): bindingId is string => Boolean(bindingId))
    )

    const actionableValidationAttachments = validationAttachments.filter((attachment) => (
      attachment.state === 'active' && Boolean(attachment.validator_binding_id)
    ))

    const validationGroups: ValidationAttachmentGroup[] = actionableValidationAttachments.map((attachment) => {
      const existingGroup = existingGroups.find((group) => (
        group.attachment_id === attachment.attachment_id
        || (attachment.validator_binding_id && group.binding_id === attachment.validator_binding_id)
      ))
      const replacement = attachment.validator_binding_id
        ? replacementsByBinding.get(attachment.validator_binding_id)
        : undefined
      const state: ValidationAttachmentGroup['state'] = replacement
        ? 'replaced'
        : existingGroup?.state === 'supplemental'
          ? 'supplemental'
          : attachment.enabled
            ? 'automatic'
            : 'skipped'
      const preserveSupplementalLink = state === 'supplemental'

      return {
        group_id: existingGroup?.group_id ?? attachment.attachment_id,
        state,
        binding_id: attachment.validator_binding_id,
        attachment_id: attachment.attachment_id,
        edge_id: replacement?.edgeId ?? (preserveSupplementalLink ? existingGroup?.edge_id : undefined),
        validator_node_id: replacement?.target ?? (preserveSupplementalLink ? existingGroup?.validator_node_id : undefined),
        label: attachment.label,
        required: attachment.required,
        blocking: attachment.blocking,
        allow_opt_out: attachment.allow_opt_out,
      }
    })

    replacementsByBinding.forEach((replacement, bindingId) => {
      if (declaredBindingIds.has(bindingId)) return

      const existingGroup = existingGroups.find((group) => (
        group.state === 'supplemental'
        && (group.binding_id === bindingId || group.edge_id === replacement.edgeId)
      ))

      validationGroups.push({
        group_id: existingGroup?.group_id ?? `edge:${replacement.edgeId}`,
        state: 'supplemental',
        binding_id: bindingId,
        attachment_id: existingGroup?.attachment_id ?? null,
        edge_id: replacement.edgeId,
        validator_node_id: replacement.target,
        replaces_attachment_id: existingGroup?.replaces_attachment_id,
        label: existingGroup?.label,
        required: false,
        blocking: false,
        allow_opt_out: false,
      })
    })

    const nullableEqual = <T,>(left: T | null | undefined, right: T | null | undefined) => (
      (left ?? undefined) === (right ?? undefined)
    )

    if (
      node.data.validation_groups?.length === validationGroups.length
      && node.data.validation_groups.every((group, index) => (
        group.group_id === validationGroups[index].group_id
        && group.state === validationGroups[index].state
        && nullableEqual(group.binding_id, validationGroups[index].binding_id)
        && nullableEqual(group.attachment_id, validationGroups[index].attachment_id)
        && nullableEqual(group.edge_id, validationGroups[index].edge_id)
        && nullableEqual(group.validator_node_id, validationGroups[index].validator_node_id)
        && nullableEqual(group.replaces_attachment_id, validationGroups[index].replaces_attachment_id)
        && nullableEqual(group.label, validationGroups[index].label)
        && group.required === validationGroups[index].required
        && group.blocking === validationGroups[index].blocking
        && group.allow_opt_out === validationGroups[index].allow_opt_out
      ))
    ) {
      return node
    }

    changed = true
    return {
      ...node,
      data: {
        ...node.data,
        validation_groups: validationGroups,
      },
    }
  })

  return changed ? nextNodes : currentNodes
}

/**
 * Pure function to compute validation errors for all nodes.
 * Takes nodes/edges as arguments to avoid closure over React state.
 * Returns array of { nodeId, message } for nodes with configuration errors.
 */
const computeValidationErrors = (
  currentNodes: AgentNode[],
  currentEdges: FlowEdge[],
): Array<{ nodeId: string; message: string }> => {
  const errors: Array<{ nodeId: string; message: string }> = []

  // Check task_input node
  const taskInputNode = currentNodes.find(n => n.data.agent_id === 'task_input')
  if (taskInputNode && !taskInputNode.data.task_instructions?.trim()) {
    errors.push({
      nodeId: taskInputNode.id,
      message: 'Initial instructions are required',
    })
  }

  if (taskInputNode) {
    const topology = projectExecutableFlowGraph(
      currentNodes.map(node => ({
        id: node.id,
        type: node.type ?? (node.data.agent_id === 'task_input' ? 'task_input' : 'agent'),
        data: node.data,
      })),
      currentEdges.map(edge => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        role: edgeRole(edge),
        satisfies_binding_id: edge.data?.satisfies_binding_id,
        replaces_attachment_id: edge.data?.replaces_attachment_id,
      })),
      taskInputNode.id,
      '1.1',
    )
    topology.issues.forEach(topologyIssue => {
      errors.push({
        nodeId: topologyIssue.node_ids[0] ?? taskInputNode.id,
        message: `[${topologyIssue.code}] ${topologyIssue.message}`,
      })
    })
  }

  return errors
}

const BuilderContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
}))

// Single unified toolbar
const Toolbar = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  height: 32,
  padding: theme.spacing(0, 0.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
  backgroundColor: alpha(theme.palette.background.default, 0.4),
  gap: theme.spacing(0.25),
}))

// Menu trigger button (File, Edit)
const MenuTrigger = styled('button')(({ theme }) => ({
  display: 'inline-flex',
  alignItems: 'center',
  padding: theme.spacing(0.25, 1),
  border: 0,
  background: 'transparent',
  fontFamily: 'inherit',
  fontSize: '0.8rem',
  fontWeight: 500,
  cursor: 'pointer',
  borderRadius: 3,
  color: theme.palette.text.secondary,
  transition: 'all 0.1s ease',
  userSelect: 'none',
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, 0.8),
    color: theme.palette.text.primary,
  },
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: 1,
  },
}))

const FileActionStrip = styled(Box)(({ theme }) => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: theme.spacing(0.25),
  marginLeft: theme.spacing(0.5),
  paddingLeft: theme.spacing(0.75),
  borderLeft: `1px solid ${theme.palette.divider}`,
}))

const FileActionButton = styled(IconButton)(({ theme }) => ({
  width: 26,
  height: 26,
  padding: 0,
  borderRadius: 4,
  color: theme.palette.text.secondary,
  '& .MuiSvgIcon-root': {
    fontSize: 17,
  },
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, 0.8),
    color: theme.palette.text.primary,
  },
  '&.Mui-disabled': {
    color: theme.palette.text.disabled,
  },
}))

// Status area on the right side of toolbar
const ToolbarStatus = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
  marginLeft: 'auto',
  paddingRight: theme.spacing(1),
  color: theme.palette.text.secondary,
  fontSize: '0.75rem',
}))

// Styled menu with professional appearance
const StyledMenu = styled(Menu)(({ theme }) => ({
  '& .MuiPaper-root': {
    minWidth: 200,
    backgroundColor: theme.palette.background.paper,
    border: `1px solid ${theme.palette.divider}`,
    boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
    borderRadius: 6,
    marginTop: 2,
  },
  '& .MuiList-root': {
    padding: theme.spacing(0.5, 0),
  },
}))

// Menu item with keyboard shortcut support
const StyledMenuItem = styled(MenuItem)(({ theme }) => ({
  padding: theme.spacing(0.5, 1.5),
  minHeight: 28,
  fontSize: '0.8rem',
  display: 'flex',
  justifyContent: 'space-between',
  gap: theme.spacing(3),
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.12),
  },
  '&.Mui-disabled': {
    opacity: 0.4,
  },
}))

// Keyboard shortcut hint
const Shortcut = styled(Typography)(({ theme }) => ({
  fontSize: '0.7rem',
  fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
  color: theme.palette.text.disabled,
  marginLeft: 'auto',
}))

const BuilderContent = styled(Box)(() => ({
  flex: 1,
  display: 'flex',
  minHeight: 0,
  overflow: 'hidden',
  position: 'relative',
}))

const CanvasArea = styled(Box)(({ theme }) => ({
  flex: 1,
  position: 'relative',
  backgroundColor: alpha(theme.palette.background.default, 0.5),
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: -2,
  },
  '& .react-flow__attribution': {
    display: 'none',
  },
}))

const SidePanel = styled(Box)(({ theme }) => ({
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(1),
  padding: theme.spacing(1),
  overflow: 'hidden',
}))

// Resize handle for palette/canvas divider
const ResizeHandle = styled(PanelResizeHandle)(({ theme }) => ({
  width: 4,
  flex: '0 0 4px',
  backgroundColor: theme.palette.divider,
  cursor: 'col-resize',
  transition: 'background-color 0.2s ease',
  borderRadius: theme.shape.borderRadius,
  position: 'relative',
  '&:hover, &[data-resize-handle-active="true"]': {
    backgroundColor: theme.palette.primary.main,
  },
  '&::after': {
    content: '""',
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: 2,
    height: 32,
    borderRadius: 1,
    backgroundColor: alpha(theme.palette.common.white, 0.45),
    pointerEvents: 'none',
  },
}))

// Custom node types for React Flow
const nodeTypes = {
  agent: FlowNode,
  output: FlowNode,
  task_input: FlowNode,  // Uses same component with conditional styling
}

// Custom edge types for React Flow - deletable edges with X button on hover
const edgeTypes = {
  deletable: DeletableEdge,
}

const isEditableShortcutTarget = (target: EventTarget | null): boolean => {
  if (!(target instanceof Element)) return false
  if (target instanceof HTMLElement && target.isContentEditable) return true

  return Boolean(target.closest('input, textarea, select, [contenteditable]'))
}

const getPrimaryShortcutLabel = (): 'Ctrl' | 'Cmd' => {
  if (typeof navigator === 'undefined') return 'Ctrl'
  return /Mac|iPhone|iPad|iPod/i.test(navigator.platform) ? 'Cmd' : 'Ctrl'
}

function FlowBuilderInner({ flowId, onFlowSaved, onFlowChange, onVerifyRequest }: FlowBuilderProps) {
  const { agents: agentMetadata } = useAgentMetadata()

  const isValidationAgentDynamic = useCallback(
    (agentId: string): boolean => isValidationAgentFromMetadata(agentId, agentMetadata),
    [agentMetadata]
  )
  const canSourceOutputAttachmentDynamic = useCallback(
    (agentId: string): boolean => canSourceOutputAttachmentFromMetadata(agentId, agentMetadata),
    [agentMetadata]
  )
  const isOutputFormatterAgentDynamic = useCallback(
    (agentId: string): boolean => isOutputFormatterAgentFromMetadata(agentId, agentMetadata),
    [agentMetadata]
  )

  // React Flow state
  const builderRootRef = useRef<HTMLDivElement>(null)
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const primaryShortcutLabel = useMemo(getPrimaryShortcutLabel, [])
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<AgentNodeData>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge['data']>([])

  // Node ID counter - useRef to persist across renders without causing HMR issues
  const nodeIdRef = useRef(0)
  const getNodeId = useCallback(() => `node_${nodeIdRef.current++}`, [])

  // UI state
  const [flowName, setFlowName] = useState('New Flow')
  const [flowDescription, setFlowDescription] = useState('')
  const [taskInstructionsDefaultOnly, setTaskInstructionsDefaultOnly] = useState(false)
  const [selectedNode, setSelectedNode] = useState<AgentNode | null>(null)
  const [paletteCollapsed, setPaletteCollapsed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [snackbar, setSnackbar] = useState<{
    message: string
    severity: 'success' | 'warning' | 'error'
  } | null>(null)

  // Prompt viewer state
  const [promptViewerOpen, setPromptViewerOpen] = useState(false)
  const [promptViewerAgent, setPromptViewerAgent] = useState<{ id: string; name: string } | null>(null)
  const [domainEnvelopeViewerNodeId, setDomainEnvelopeViewerNodeId] = useState<string | null>(null)
  const [domainEnvelopeViewerOpen, setDomainEnvelopeViewerOpen] = useState(false)

  // Menu bar state
  const [fileMenuAnchor, setFileMenuAnchor] = useState<HTMLElement | null>(null)
  const [editMenuAnchor, setEditMenuAnchor] = useState<HTMLElement | null>(null)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // Open Flow Dialog state
  const [openDialogOpen, setOpenDialogOpen] = useState(false)
  const [savedFlows, setSavedFlows] = useState<FlowSummaryResponse[]>([])
  const [loadingFlows, setLoadingFlows] = useState(false)
  const [flowSearchTerm, setFlowSearchTerm] = useState('')

  // Save Dialog state (for save/save-as naming)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [saveDialogName, setSaveDialogName] = useState('')
  const [saveDialogMode, setSaveDialogMode] = useState<'save' | 'save_as'>('save')
  const [bindingDialog, setBindingDialog] = useState<{
    connection: Connection
    bindings: ValidationAttachmentSelection[]
  } | null>(null)

  const handleDeleteEdge = useCallback((edgeId: string) => {
    setEdges((currentEdges) => currentEdges.filter((edge) => edge.id !== edgeId))
  }, [setEdges])

  const canvasEdges = useMemo(
    () => edges.map((edge) => ({
      ...edge,
      data: {
        ...edge.data,
        onDeleteEdge: handleDeleteEdge,
      },
    })),
    [edges, handleDeleteEdge]
  )

  const outputBindingsByNodeId = useMemo(() => {
    const bindings = new Map<string, OutputBindingView>()
    const flowNodes = nodes as AgentNode[]
    const flowEdges = edges as FlowEdge[]
    const flowNodeById = new Map(flowNodes.map((node) => [node.id, node]))

    for (const node of flowNodes) {
      if (node.type !== 'output' && !isOutputFormatterAgentDynamic(node.data.agent_id)) continue

      const attachmentEdges = flowEdges.filter(
        (edge) => edge.target === node.id && edgeRole(edge) === 'output_attachment'
      )
      if (attachmentEdges.length === 0) {
        bindings.set(node.id, { status: 'missing', sources: [] })
        continue
      }

      const sourceTargetKeys = new Set<string>()
      const hasDuplicateSource = attachmentEdges.some((edge) => {
        const key = `${edge.source}\u0000${edge.target}`
        if (sourceTargetKeys.has(key)) return true
        sourceTargetKeys.add(key)
        return false
      })
      const sources = attachmentEdges.map((edge) => {
        const source = flowNodeById.get(edge.source)
        return {
          node: source,
          sourceNodeId: edge.source,
          sourceLabel: source?.data.agent_display_name ?? edge.source,
        }
      })
      if (hasDuplicateSource) {
        bindings.set(node.id, {
          status: 'duplicate',
          sources: sources.map(({ sourceNodeId, sourceLabel }) => ({ sourceNodeId, sourceLabel })),
        })
        continue
      }

      const incompatibleSource = sources.find(({ node: source }) => (
        !source || !canSourceOutputAttachmentDynamic(source.data.agent_id)
      ))
      if (incompatibleSource) {
        bindings.set(node.id, {
          status: 'incompatible',
          sources: sources.map(({ sourceNodeId, sourceLabel }) => ({ sourceNodeId, sourceLabel })),
        })
        continue
      }

      const sourceViews = sources.map(({ sourceNodeId, sourceLabel }) => ({
        sourceNodeId,
        sourceLabel,
      }))
      bindings.set(node.id, {
        status: 'bound',
        sources: sourceViews,
        ...(sourceViews.length === 1
          ? {
              sourceNodeId: sourceViews[0].sourceNodeId,
              sourceLabel: sourceViews[0].sourceLabel,
            }
          : {}),
      })
    }

    return bindings
  }, [nodes, edges, canSourceOutputAttachmentDynamic, isOutputFormatterAgentDynamic])

  const canvasNodes = useMemo(
    () => (nodes as AgentNode[]).map((node) => {
      const outputBinding = outputBindingsByNodeId.get(node.id)
      if (!outputBinding) return node
      return { ...node, data: { ...node.data, outputBinding } }
    }),
    [nodes, outputBindingsByNodeId]
  )

  const selectedEditorNode = useMemo(() => {
    if (!selectedNode) return null
    return (nodes.find((node) => node.id === selectedNode.id) as AgentNode | undefined) ?? null
  }, [nodes, selectedNode])

  const selectedOutputBinding = selectedEditorNode
    ? outputBindingsByNodeId.get(selectedEditorNode.id)
    : undefined

  // Manage Flows Dialog state
  const [manageDialogOpen, setManageDialogOpen] = useState(false)
  const [manageFlows, setManageFlows] = useState<FlowSummaryResponse[]>([])
  const [loadingManageFlows, setLoadingManageFlows] = useState(false)
  const [editingFlowId, setEditingFlowId] = useState<string | null>(null)
  const [editingFlowName, setEditingFlowName] = useState('')
  const [renamingFlow, setRenamingFlow] = useState(false)
  const [deleteManageConfirmOpen, setDeleteManageConfirmOpen] = useState(false)
  const [flowToDeleteFromManage, setFlowToDeleteFromManage] = useState<FlowSummaryResponse | null>(null)
  const [deletingFromManage, setDeletingFromManage] = useState(false)

  // Current flow ID (null for new flow)
  const [currentFlowId, setCurrentFlowId] = useState<string | null>(flowId || null)

  // Ref for cleanup of setTimeout
  const fitViewTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Ref for revalidation timeout cleanup (used in loadFlow and handleNodeDataUpdate)
  const revalidateTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Ref to hold revalidateValidators function (defined later, needed in loadFlow)
  const revalidateValidatorsRef = useRef<(() => void) | null>(null)

  // Load flow from API
  const loadFlow = useCallback(async (id: string) => {
    setLoading(true)
    try {
      const flow = await getFlow(id)
      if (flow.flow_definition.version !== '1.1') {
        setSnackbar({
          message: unsupportedFlowVersionMessage(flow.name, flow.flow_definition.version),
          severity: 'error',
        })
        return
      }
      setFlowName(flow.name)
      setFlowDescription(flow.description || '')
      setTaskInstructionsDefaultOnly(flow.flow_definition.task_instructions_default_only === true)
      setCurrentFlowId(flow.id)

      // Convert flow definition to React Flow format
      const flowNodes = flow.flow_definition.nodes.map((n) => (
        {
          id: n.id,
          type: n.type === 'task_input'
            ? 'task_input'
            : n.type === 'output'
              || isOutputFormatterAgentFromMetadata(n.data.agent_id, agentMetadata)
              ? 'output'
              : 'agent',
          position: n.position,
          data: n.data,
        }
      ))
      const flowEdges = flow.flow_definition.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'deletable',
        animated: (e.role ?? 'control_flow') === 'control_flow',
        data: {
          role: e.role ?? 'control_flow',
          satisfies_binding_id: e.satisfies_binding_id,
          replaces_attachment_id: e.replaces_attachment_id,
        },
      }))

      setNodes(flowNodes)
      setEdges(flowEdges)
      if (flow.validation_warnings?.length) {
        const warningMessages = flow.validation_warnings.map((warning) => warning.message).join(' ')
        const hasCriticalWarning = flow.has_critical_issues
        setSnackbar({
          message: `Flow loaded with validation ${hasCriticalWarning ? 'issue' : 'warning'}: ${warningMessages}`,
          severity: hasCriticalWarning ? 'error' : 'warning',
        })
      }

      // Update nodeId counter to avoid collisions
      const maxId = Math.max(...flowNodes.map((n) => parseInt(n.id.replace('node_', '')) || 0), 0)
      nodeIdRef.current = maxId + 1

      // Fit view after loading (with slight delay to ensure nodes are rendered)
      // Clear any existing timeout first
      if (fitViewTimeoutRef.current) {
        clearTimeout(fitViewTimeoutRef.current)
      }
      fitViewTimeoutRef.current = setTimeout(() => {
        reactFlowInstance?.fitView({ padding: 0.2, maxZoom: 1 })
        fitViewTimeoutRef.current = null
      }, 50)

      // Trigger validation after load to ensure error banners reflect current state
      // This handles cases where loaded flow has same node/edge count as previous
      if (revalidateTimeoutRef.current) {
        clearTimeout(revalidateTimeoutRef.current)
      }
      revalidateTimeoutRef.current = setTimeout(() => {
        revalidateValidatorsRef.current?.()
        revalidateTimeoutRef.current = null
      }, 100)
    } catch (err) {
      logger.error('Failed to load flow', err as Error, { component: 'FlowBuilder' })
      setSnackbar({ message: 'Failed to load flow', severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [setNodes, setEdges, reactFlowInstance, agentMetadata])

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (fitViewTimeoutRef.current) {
        clearTimeout(fitViewTimeoutRef.current)
      }
      if (revalidateTimeoutRef.current) {
        clearTimeout(revalidateTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    setNodes((currentNodes) => rebuildValidationGroupsFromEdges(
      currentNodes as AgentNode[],
      edges as FlowEdge[]
    ))
  }, [edges]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load flow if flowId provided (and different from current)
  useEffect(() => {
    // Only load if flowId is provided AND different from what we already have
    if (flowId && flowId !== currentFlowId) {
      loadFlow(flowId)
    }
  }, [flowId]) // eslint-disable-line react-hooks/exhaustive-deps
  // Note: We intentionally exclude loadFlow and currentFlowId from deps
  // to prevent re-loading after save (which updates currentFlowId)

  // Initialize new flow with task_input node when no flowId is provided
  useEffect(() => {
    // Only run once on mount when there's no flowId
    if (!flowId && nodes.length === 0) {
      setNodes([createInitialTaskInputNode()])
      nodeIdRef.current = 1
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  // Note: Only run on mount - empty deps is intentional

  // Report flow state changes to parent (for context sharing with Claude)
  useEffect(() => {
    if (onFlowChange) {
      const flowState: FlowState = {
        flowName,
        version: '1.1',
        entry_node_id: nodes.find(node => node.data.agent_id === 'task_input')?.id,
        nodes: nodes.map((n) => ({
          id: n.id,
          type: (n.type ?? 'agent') as NodeType,
          agent_id: n.data.agent_id,
          agent_display_name: n.data.agent_display_name,
          task_instructions: n.data.task_instructions,
          custom_instructions: n.data.custom_instructions,
          include_evidence: n.data.include_evidence,
          output_filename_template: n.data.output_filename_template,
          projection_plan: n.data.projection_plan,
          output_key: n.data.output_key,
          validation_attachments: n.data.validation_attachments,
          validation_groups: n.data.validation_groups,
        })),
        edges: edges.map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          role: edgeRole(e as FlowEdge),
          satisfies_binding_id: e.data?.satisfies_binding_id,
          replaces_attachment_id: e.data?.replaces_attachment_id,
        })),
      }
      onFlowChange(flowState)
    }
  }, [nodes, edges, flowName, onFlowChange])

  // Update hasError/errorMessage on nodes based on current validation state
  // Called when extractors are added/removed, connections change, or node data updates
  // Uses setNodes functional update to access current state and avoid stale closures
  const revalidateValidators = useCallback(() => {
    setNodes(currentNodes => {
      const errors = computeValidationErrors(
        currentNodes as AgentNode[],
        edges as FlowEdge[],
      )
      const errorsByNodeId = new Map(errors.map(e => [e.nodeId, e.message]))

      return currentNodes.map(node => {
        const errorMessage = errorsByNodeId.get(node.id)
        const hasError = Boolean(errorMessage)

        // Only update if changed to avoid unnecessary re-renders
        if (node.data.hasError !== hasError || node.data.errorMessage !== errorMessage) {
          return {
            ...node,
            data: {
              ...node.data,
              hasError,
              errorMessage: errorMessage || undefined,
            },
          }
        }
        return node
      })
    })
  }, [setNodes, edges]) // Depends on edge topology and current node data

  // Keep ref in sync for use in loadFlow (which is defined before this callback)
  useEffect(() => {
    revalidateValidatorsRef.current = revalidateValidators
  }, [revalidateValidators])

  const refreshFlowLists = useCallback(async () => {
    const response = await listFlows()
    setSavedFlows(response.flows)
    setManageFlows(response.flows)
    return response.flows
  }, [])

  // Stable key for edge topology - triggers revalidation when edges are rewired (not just added/removed)
  // Includes handles to catch rewires between different connection points on the same nodes
  const edgeTopologyKey = useMemo(
    () => edges.map((edge) => (
      `${edgeRole(edge as FlowEdge)}:${edge.source}:${edge.sourceHandle ?? ''}-${edge.target}:${edge.targetHandle ?? ''}`
    )).sort().join(','),
    [edges]
  )

  // Re-validate when nodes or edges change (extractors added/removed, connections changed/rewired)
  useEffect(() => {
    // Debounce to avoid excessive re-validation during rapid changes
    const timeoutId = setTimeout(revalidateValidators, 100)
    return () => clearTimeout(timeoutId)
  }, [nodes.length, edgeTopologyKey]) // eslint-disable-line react-hooks/exhaustive-deps
  // Note: We deliberately exclude revalidateValidators to avoid re-render loops

  // Save flow to API (nameOverride allows passing name directly; forceCreate enables Save As behavior)
  const handleSave = async (
    nameOverride?: string,
    options?: { forceCreate?: boolean }
  ) => {
    const forceCreate = options?.forceCreate ?? false
    const nameToUse = nameOverride || flowName

    if (!nameToUse.trim()) {
      setSnackbar({ message: 'Please enter a flow name', severity: 'error' })
      return
    }

    if (nodes.length === 0) {
      setSnackbar({ message: 'Add at least one agent to the flow', severity: 'error' })
      return
    }

    // Check for task_input node existence (structure validation)
    const taskInputNode = nodes.find(n => n.data.agent_id === 'task_input')
    if (!taskInputNode) {
      setSnackbar({
        message: 'Flow requires a Task Input node. Add "Initial Instructions" from the catalog.',
        severity: 'error'
      })
      return
    }

    // Compute validation errors fresh (don't rely on stale hasError state)
    const validationErrors = computeValidationErrors(
      nodes as AgentNode[],
      edges as FlowEdge[],
    )
    if (validationErrors.length > 0) {
      // Find the first error node and select it
      const firstError = validationErrors[0]
      const errorNode = nodes.find(n => n.id === firstError.nodeId) as AgentNode | undefined
      if (errorNode) {
        setSelectedNode(errorNode)
      }
      setSnackbar({
        message: firstError.message,
        severity: 'error'
      })
      return
    }

    setSaving(true)
    try {
      // Convert to API format
      // Entry node is the task_input node (already validated above)
      const entryNodeId = taskInputNode.id

      const flowDefinition: FlowDefinition = {
        version: '1.1',
        ...(taskInstructionsDefaultOnly ? { task_instructions_default_only: true } : {}),
        nodes: nodes.map((n) => ({
          id: n.id,
          type: n.data.agent_id === 'task_input'
            ? 'task_input'
            : n.type === 'output'
              ? 'output'
              : 'agent',
          position: n.position,
          data: flowNodeDataForPersistence(n.data),
        })),
        edges: edges.map((e) => {
          const role = edgeRole(e as FlowEdge)
          return {
            id: e.id,
            source: e.source,
            target: e.target,
            role,
            satisfies_binding_id: role === 'validation_attachment'
              ? e.data?.satisfies_binding_id
              : undefined,
            replaces_attachment_id: role === 'validation_attachment'
              ? e.data?.replaces_attachment_id
              : undefined,
          }
        }),
        entry_node_id: entryNodeId,
      }

      let savedFlow: FlowResponse
      if (currentFlowId && !forceCreate) {
        savedFlow = await updateFlow(currentFlowId, {
          name: nameToUse,
          description: flowDescription || undefined,
          flow_definition: flowDefinition,
        })
      } else {
        savedFlow = await createFlow({
          name: nameToUse,
          description: flowDescription || undefined,
          flow_definition: flowDefinition,
        })
        setCurrentFlowId(savedFlow.id)
      }

      const flowMutationReason = currentFlowId && !forceCreate ? 'updated' : 'created'
      await refreshFlowLists()

      // Update flowName state to match saved name
      setFlowName(savedFlow.name)
      setFlowDescription(savedFlow.description || '')
      setTaskInstructionsDefaultOnly(
        savedFlow.flow_definition.task_instructions_default_only === true
      )
      notifyFlowListInvalidated({
        flowId: savedFlow.id,
        reason: flowMutationReason,
      })
      setSnackbar({
        message: forceCreate ? 'Flow saved as new flow' : 'Flow saved successfully',
        severity: 'success'
      })
      onFlowSaved?.(savedFlow.id)
    } catch (err) {
      logger.error('Failed to save flow', err as Error, { component: 'FlowBuilder' })
      const errorMessage = err instanceof Error ? err.message : 'Failed to save flow'
      setSnackbar({ message: errorMessage, severity: 'error' })
    } finally {
      setSaving(false)
    }
  }

  // Handle new flow
  const handleNewFlow = () => {
    setNodes([createInitialTaskInputNode()])
    setEdges([])
    setFlowName('New Flow')
    setFlowDescription('')
    setTaskInstructionsDefaultOnly(false)
    setCurrentFlowId(null)
    setSelectedNode(null)
    nodeIdRef.current = 1  // Start from 1 since node_0 is used
  }

  const addValidationAttachmentEdge = useCallback(
    (connection: Connection, binding: ValidationAttachmentSelection) => {
      if (!connection.source || !connection.target || !binding.validator_binding_id) return
      const source = connection.source
      const target = connection.target
      const bindingId = binding.validator_binding_id
      const existing = edges as FlowEdge[]

      const duplicate = existing.some((edge) => (
        edgeRole(edge) === 'validation_attachment'
        && edge.source === source
        && edge.data?.satisfies_binding_id === bindingId
      ))
      if (duplicate) {
        setSnackbar({
          message: `"${validationEdgeLabel(binding)}" already has a custom validator edge from this extractor.`,
          severity: 'error',
        })
        return
      }

      const edge: FlowEdge = {
        ...connection,
        id: nextValidationEdgeId(existing),
        source,
        target,
        type: 'deletable',
        animated: false,
        data: {
          role: 'validation_attachment',
          satisfies_binding_id: bindingId,
          validationLabel: validationEdgeLabel(binding),
        },
      }
      const nextEdges = [...existing, edge]
      setEdges(nextEdges)
      setNodes((currentNodes) => rebuildValidationGroupsFromEdges(
        currentNodes as AgentNode[],
        nextEdges
      ))
    },
    [edges, setEdges, setNodes]
  )

  const handleBindingDialogClose = useCallback(() => {
    setBindingDialog(null)
  }, [])

  const handleBindingDialogSelect = useCallback((binding: ValidationAttachmentSelection) => {
    if (!bindingDialog) return
    addValidationAttachmentEdge(bindingDialog.connection, binding)
    setBindingDialog(null)
  }, [addValidationAttachmentEdge, bindingDialog])

  // Handle connection between nodes
  const onConnect = useCallback(
    (params: Connection) => {
      const sourceNode = params.source
        ? nodes.find((n) => n.id === params.source) as AgentNode | undefined
        : undefined
      const targetNode = params.target
        ? nodes.find((n) => n.id === params.target) as AgentNode | undefined
        : undefined

      // Prevent connections TO task_input nodes (they can only have outgoing connections)
      if (targetNode?.data.agent_id === 'task_input') {
        setSnackbar({ message: 'Initial Instructions node cannot have incoming connections', severity: 'error' })
        return
      }

      if (sourceNode?.type === 'output') {
        setSnackbar({
          message: 'Output formatters are terminal attachments and cannot connect to another step.',
          severity: 'error',
        })
        return
      }

      if (targetNode?.type === 'output') {
        if (!sourceNode || !canSourceOutputAttachmentDynamic(sourceNode.data.agent_id)) {
          setSnackbar({
            message: (
              'Attach this formatter to an extraction result or to an active validation '
              + 'result with a declared output schema.'
            ),
            severity: 'error',
          })
          return
        }
        const duplicateAttachment = (edges as FlowEdge[]).some((edge) => (
          edgeRole(edge) === 'output_attachment'
          && edge.source === sourceNode.id
          && edge.target === targetNode.id
        ))
        if (duplicateAttachment) {
          setSnackbar({
            message: 'This source step is already attached to the formatter.',
            severity: 'error',
          })
          return
        }
        setEdges((currentEdges) => [
          ...currentEdges,
          {
            ...params,
            id: nextOutputEdgeId(currentEdges as FlowEdge[]),
            source: sourceNode.id,
            target: targetNode.id,
            type: 'deletable',
            animated: false,
            data: { role: 'output_attachment' },
          } as FlowEdge,
        ])
        return
      }

      const bindingOptions = activeValidationBindingOptions(sourceNode)
      const shouldCreateValidationAttachmentEdge = Boolean(
        sourceNode
        && targetNode
        && bindingOptions.length > 0
        && isValidationAgentDynamic(targetNode.data.agent_id)
      )

      if (shouldCreateValidationAttachmentEdge) {
        if (bindingOptions.length === 1) {
          addValidationAttachmentEdge(params, bindingOptions[0])
        } else {
          setBindingDialog({ connection: params, bindings: bindingOptions })
        }
        return
      }

      setEdges((eds) => addEdge({
        ...params,
        animated: true,
        type: 'deletable',
        data: { role: 'control_flow' },
      } as FlowEdge, eds))
    },
    [
      setEdges,
      nodes,
      edges,
      canSourceOutputAttachmentDynamic,
      isValidationAgentDynamic,
      addValidationAttachmentEdge,
    ]
  )

  // Handle node selection
  const onNodeClick = useCallback((_event: React.MouseEvent, node: { id: string; data: AgentNodeData }) => {
    // Cast to our AgentNode type (React Flow types are generic)
    setSelectedNode(node as AgentNode)
  }, [])

  // Handle canvas click (deselect)
  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  // Handle drag over (allow drop)
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  // Handle drop from palette
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()

      if (!reactFlowInstance || !reactFlowWrapper.current) return

      const data = event.dataTransfer.getData('application/reactflow')
      if (!data) return

      try {
        const { type, agentId, agentName, agentDescription, promptVersion } = JSON.parse(data) as {
          type: 'agent' | 'task_input'
          agentId: string
          agentName: string
          agentDescription: string
          promptVersion?: number
        }
        if (type !== 'agent' && type !== 'task_input') return

        // Check if dropping task_input and one already exists
        if (type === 'task_input' || agentId === 'task_input') {
          const existingTaskInput = nodes.find((n) => n.data.agent_id === 'task_input')
          if (existingTaskInput) {
            setSnackbar({ message: 'Only one Initial Instructions node allowed per flow', severity: 'error' })
            return
          }
        }

        // screenToFlowPosition expects raw screen coordinates (clientX/clientY)
        const dropPosition = reactFlowInstance.screenToFlowPosition({
          x: event.clientX,
          y: event.clientY,
        })

        // Offset by half the node size so it centers on the drop point
        const NODE_WIDTH = 160  // Average of minWidth (140) and maxWidth (180)
        const NODE_HEIGHT = 60  // Approximate height based on content
        const position = {
          x: dropPosition.x - NODE_WIDTH / 2,
          y: dropPosition.y - NODE_HEIGHT / 2,
        }

        const isTaskInput = type === 'task_input' || agentId === 'task_input'
        const isOutputFormatter = !isTaskInput
          && isOutputFormatterAgentDynamic(agentId)
        const validationAttachments = isTaskInput
          ? []
          : buildDefaultValidationSelections(agentId, agentMetadata)

        const newNodeId = getNodeId()

        const newNode: AgentNode = {
          id: newNodeId,
          type: isTaskInput ? 'task_input' : isOutputFormatter ? 'output' : 'agent',
          position,
          data: {
            agent_id: agentId,
            agent_display_name: agentName,
            agent_description: agentDescription,
            task_instructions: isTaskInput ? '' : undefined,
            custom_instructions: '',
            prompt_version: promptVersion,
            include_evidence: isTaskInput
              ? undefined
              : resolveOutputFormatterIncludeEvidence(agentId, agentMetadata),
            output_key: isTaskInput ? 'task_input' : `${agentId.replace(/-/g, '_')}_output`,
            validation_attachments: validationAttachments.length > 0
              ? validationAttachments
              : undefined,
          },
        }

        setNodes((nds) => [...nds, newNode])
      } catch (err) {
        logger.error('Failed to parse drag data', err as Error, { component: 'FlowBuilder' })
      }
    },
    [
      reactFlowInstance,
      setNodes,
      getNodeId,
      agentMetadata,
      isOutputFormatterAgentDynamic,
    ]
  )

  // Handle node data update from editor
  const handleNodeDataUpdate = useCallback(
    (nodeId: string, data: Partial<AgentNodeData>) => {
      setNodes((nds) => {
        const updatedNodes = nds.map((node) =>
          node.id === nodeId
            ? { ...node, data: { ...node.data, ...data } }
            : node
        )
        return rebuildValidationGroupsFromEdges(updatedNodes as AgentNode[], edges as FlowEdge[])
      })
      // Trigger revalidation after data update so error banners update immediately
      // Clear any pending revalidation timeout to avoid stale updates
      if (revalidateTimeoutRef.current) {
        clearTimeout(revalidateTimeoutRef.current)
      }
      revalidateTimeoutRef.current = setTimeout(() => {
        revalidateValidators()
        revalidateTimeoutRef.current = null
      }, 50)
    },
    [setNodes, edges, revalidateValidators]
  )

  // Handle node deletion by ID (for NodeEditor delete button)
  const handleDeleteNode = useCallback((nodeId: string) => {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId))
    setEdges((eds) =>
      eds.filter((e) => e.source !== nodeId && e.target !== nodeId)
    )
    setSelectedNode(null)
  }, [setNodes, setEdges])

  const domainEnvelopeViewerNode = useMemo(() => {
    if (!domainEnvelopeViewerNodeId) return null
    return nodes.find((node) => node.id === domainEnvelopeViewerNodeId) ?? null
  }, [nodes, domainEnvelopeViewerNodeId])

  const domainEnvelopeViewerMetadata = domainEnvelopeViewerNode
    ? agentMetadata[domainEnvelopeViewerNode.data.agent_id]?.domain_envelope
    : undefined

  // Handle marking a node as manually configured when user saves in NodeEditor
  // This prevents auto-switching on future connections
  // Handle opening prompt viewer
  const handleViewPrompts = useCallback((agentId: string, agentName: string) => {
    setPromptViewerAgent({ id: agentId, name: agentName })
    setPromptViewerOpen(true)
  }, [])

  // Handle closing prompt viewer
  const handleClosePromptViewer = useCallback(() => {
    setPromptViewerOpen(false)
  }, [])

  // Handle opening domain envelope viewer
  const handleViewDomainEnvelope = useCallback((nodeId: string) => {
    setDomainEnvelopeViewerNodeId(nodeId)
    setDomainEnvelopeViewerOpen(true)
  }, [])

  // Handle closing domain envelope viewer
  const handleCloseDomainEnvelopeViewer = useCallback(() => {
    setDomainEnvelopeViewerOpen(false)
  }, [])

  // File menu handlers
  const handleFileMenuOpen = useCallback((event: React.MouseEvent<HTMLElement>) => {
    setFileMenuAnchor(event.currentTarget)
  }, [])

  const handleFileMenuClose = useCallback(() => {
    setFileMenuAnchor(null)
  }, [])

  // Open Flow Dialog handlers
  const handleOpenDialogOpen = useCallback(() => {
    setFileMenuAnchor(null)
    setOpenDialogOpen(true)
    setFlowSearchTerm('')
    // Load flows
    setLoadingFlows(true)
    refreshFlowLists()
      .catch((err: Error) => {
        logger.error('Failed to load flows', err, { component: 'FlowBuilder' })
        setSnackbar({
          message: err.message,
          severity: 'error',
        })
      })
      .finally(() => setLoadingFlows(false))
  }, [refreshFlowLists])

  const handleOpenDialogClose = useCallback(() => {
    setOpenDialogOpen(false)
  }, [])

  // Handle selecting a flow to load
  const handleSelectFlow = useCallback((selectedFlowId: string) => {
    setOpenDialogOpen(false)
    loadFlow(selectedFlowId)
  }, [loadFlow])

  // Filtered flows based on search
  const filteredFlows = useMemo(() => {
    if (!flowSearchTerm.trim()) return savedFlows
    const term = flowSearchTerm.toLowerCase()
    return savedFlows.filter((flow) => flow.name.toLowerCase().includes(term))
  }, [savedFlows, flowSearchTerm])

  // Save Dialog handlers
  const handleSaveClick = useCallback(() => {
    if (saving) return

    setFileMenuAnchor(null)
    if (nodes.length === 0) {
      setSnackbar({ message: 'Add at least one agent to the flow', severity: 'error' })
      return
    }
    // If it's an existing flow, save directly
    if (currentFlowId) {
      handleSave()
    } else {
      // New flow - show save dialog
      setSaveDialogMode('save')
      setSaveDialogName(flowName === 'New Flow' ? '' : flowName)
      setSaveDialogOpen(true)
    }
  }, [currentFlowId, flowName, nodes.length, saving, handleSave])

  const handleSaveAsClick = useCallback(() => {
    setFileMenuAnchor(null)
    if (nodes.length === 0) {
      setSnackbar({ message: 'Add at least one agent to the flow', severity: 'error' })
      return
    }

    const suggestedName = flowName === 'New Flow'
      ? ''
      : `${flowName} (Copy)`
    setSaveDialogMode('save_as')
    setSaveDialogName(suggestedName)
    setSaveDialogOpen(true)
  }, [flowName, nodes.length])

  const handleSaveDialogClose = useCallback(() => {
    setSaveDialogOpen(false)
  }, [])

  const handleSaveDialogConfirm = useCallback(() => {
    const nameToSave = saveDialogName.trim()
    if (!nameToSave) {
      setSnackbar({ message: 'Please enter a flow name', severity: 'error' })
      return
    }
    setSaveDialogOpen(false)
    // Pass name directly to handleSave to avoid async state update issues
    handleSave(nameToSave, { forceCreate: saveDialogMode === 'save_as' })
  }, [saveDialogName, saveDialogMode, handleSave])

  // Manage Flows Dialog handlers
  const handleManageDialogOpen = useCallback(() => {
    setFileMenuAnchor(null)
    setManageDialogOpen(true)
    setEditingFlowId(null)
    setEditingFlowName('')
    // Load flows
    setLoadingManageFlows(true)
    refreshFlowLists()
      .catch((err: Error) => {
        logger.error('Failed to load flows', err, { component: 'FlowBuilder' })
        setSnackbar({
          message: err.message,
          severity: 'error',
        })
      })
      .finally(() => setLoadingManageFlows(false))
  }, [refreshFlowLists])

  const handleManageDialogClose = useCallback(() => {
    setManageDialogOpen(false)
    setEditingFlowId(null)
    setEditingFlowName('')
  }, [])

  // Start editing a flow name
  const handleRenameStart = useCallback((flow: FlowSummaryResponse) => {
    setEditingFlowId(flow.id)
    setEditingFlowName(flow.name)
  }, [])

  // Cancel editing
  const handleRenameCancel = useCallback(() => {
    setEditingFlowId(null)
    setEditingFlowName('')
  }, [])

  // Confirm rename
  const handleRenameConfirm = useCallback(async () => {
    if (!editingFlowId || !editingFlowName.trim()) return

    setRenamingFlow(true)
    try {
      // First fetch the full flow to get its definition
      const fullFlow = await getFlow(editingFlowId)
      if (fullFlow.flow_definition.version !== '1.1') {
        throw new Error(unsupportedFlowVersionMessage(
          fullFlow.name,
          fullFlow.flow_definition.version,
        ))
      }
      // Update with new name
      const updatedFlow = await updateFlow(editingFlowId, {
        name: editingFlowName.trim(),
        description: fullFlow.description || undefined,
        flow_definition: fullFlow.flow_definition,
      })
      await refreshFlowLists()
      notifyFlowListInvalidated({
        flowId: updatedFlow.id,
        reason: 'updated',
      })
      // If this is the currently loaded flow, update the flowName state too
      if (editingFlowId === currentFlowId) {
        setFlowName(updatedFlow.name)
        setFlowDescription(updatedFlow.description || '')
      }
      setEditingFlowId(null)
      setEditingFlowName('')
      setSnackbar({ message: 'Flow renamed successfully', severity: 'success' })
    } catch (err) {
      logger.error('Failed to rename flow', err as Error, { component: 'FlowBuilder' })
      const errorMessage = err instanceof Error ? err.message : 'Failed to rename flow'
      setSnackbar({ message: errorMessage, severity: 'error' })
    } finally {
      setRenamingFlow(false)
    }
  }, [editingFlowId, editingFlowName, currentFlowId, refreshFlowLists])

  // Delete flow from Manage dialog
  const handleDeleteFromManageClick = useCallback((flow: FlowSummaryResponse) => {
    setFlowToDeleteFromManage(flow)
    setDeleteManageConfirmOpen(true)
  }, [])

  const handleDeleteFromManageCancel = useCallback(() => {
    setDeleteManageConfirmOpen(false)
    setFlowToDeleteFromManage(null)
  }, [])

  const handleDeleteFromManageConfirm = useCallback(async () => {
    if (!flowToDeleteFromManage) return

    setDeletingFromManage(true)
    try {
      await deleteFlow(flowToDeleteFromManage.id)
      // Remove from local list
      setManageFlows((flows) => flows.filter((f) => f.id !== flowToDeleteFromManage.id))
      setSavedFlows((flows) => flows.filter((f) => f.id !== flowToDeleteFromManage.id))
      notifyFlowListInvalidated({
        flowId: flowToDeleteFromManage.id,
        reason: 'deleted',
      })
      // If this was the currently loaded flow, clear it
      if (flowToDeleteFromManage.id === currentFlowId) {
        handleNewFlow()
      }
      setSnackbar({ message: 'Flow deleted successfully', severity: 'success' })
    } catch (err) {
      logger.error('Failed to delete flow', err as Error, { component: 'FlowBuilder' })
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete flow'
      setSnackbar({ message: errorMessage, severity: 'error' })
    } finally {
      setDeletingFromManage(false)
      setDeleteManageConfirmOpen(false)
      setFlowToDeleteFromManage(null)
    }
  }, [flowToDeleteFromManage, currentFlowId, handleNewFlow])

  // Edit menu handlers
  const handleEditMenuOpen = useCallback((event: React.MouseEvent<HTMLElement>) => {
    setEditMenuAnchor(event.currentTarget)
  }, [])

  const handleEditMenuClose = useCallback(() => {
    setEditMenuAnchor(null)
  }, [])

  // Handle select all nodes
  const handleSelectAll = useCallback(() => {
    setEditMenuAnchor(null)
    // React Flow handles selection via onNodesChange, but menu/shortcut select-all updates both collections directly.
    setNodes((nds) => nds.map((n) => ({ ...n, selected: true })))
    setEdges((eds) => eds.map((e) => ({ ...e, selected: true })))
  }, [setNodes, setEdges])

  const selectedNodeIds = useMemo(
    () => nodes.filter((n) => n.selected).map((n) => n.id),
    [nodes]
  )
  const selectedEdgeIds = useMemo(
    () => edges.filter((e) => e.selected).map((e) => e.id),
    [edges]
  )
  const selectedElementsCount = selectedNodeIds.length + selectedEdgeIds.length

  // Handle delete all selected nodes and edges (for Edit menu and keyboard shortcut)
  const handleDeleteAllSelected = useCallback(() => {
    setEditMenuAnchor(null)
    if (selectedNodeIds.length === 0 && selectedEdgeIds.length === 0) {
      setSnackbar({ message: 'No nodes or connections selected', severity: 'error' })
      return
    }

    setNodes((nds) => nds.filter((n) => !n.selected))
    setEdges((eds) => eds.filter((e) => (
      !selectedEdgeIds.includes(e.id)
      && !selectedNodeIds.includes(e.source)
      && !selectedNodeIds.includes(e.target)
    )))
    setSelectedNode(null)
  }, [selectedNodeIds, selectedEdgeIds, setNodes, setEdges])

  // Handle delete flow - show confirmation dialog
  const handleDeleteFlowClick = useCallback(() => {
    setFileMenuAnchor(null)
    if (!currentFlowId) {
      setSnackbar({ message: 'No flow to delete', severity: 'error' })
      return
    }
    setDeleteConfirmOpen(true)
  }, [currentFlowId])

  // Confirm delete flow
  const handleDeleteFlowConfirm = useCallback(async () => {
    if (!currentFlowId) return

    setDeleting(true)
    try {
      await deleteFlow(currentFlowId)
      handleNewFlow()
      setSnackbar({ message: 'Flow deleted successfully', severity: 'success' })
    } catch (err) {
      logger.error('Failed to delete flow', err as Error, { component: 'FlowBuilder' })
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete flow'
      setSnackbar({ message: errorMessage, severity: 'error' })
    } finally {
      setDeleting(false)
      setDeleteConfirmOpen(false)
    }
  }, [currentFlowId, handleNewFlow])

  // Cancel delete
  const handleDeleteFlowCancel = useCallback(() => {
    setDeleteConfirmOpen(false)
  }, [])

  useEffect(() => {
    const isBuilderShortcutContext = (event: KeyboardEvent): boolean => {
      const root = builderRootRef.current
      if (!root) return false

      const target = event.target
      if (target instanceof Node && root.contains(target)) return true

      const activeElement = document.activeElement
      return activeElement === document.body
        || (activeElement instanceof Node && root.contains(activeElement))
    }

    const isCanvasShortcutContext = (event: KeyboardEvent): boolean => {
      const canvas = reactFlowWrapper.current
      if (!canvas) return false

      const target = event.target
      if (target instanceof Node && canvas.contains(target)) return true

      const activeElement = document.activeElement
      return activeElement instanceof Node && canvas.contains(activeElement)
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (isEditableShortcutTarget(event.target)) return
      if (!isBuilderShortcutContext(event)) return

      const isPrimaryModifier = event.ctrlKey || event.metaKey
      const key = event.key.toLowerCase()

      if (isPrimaryModifier && !event.shiftKey && key === 's') {
        event.preventDefault()
        handleSaveClick()
        return
      }

      if (!isCanvasShortcutContext(event)) return

      if (event.key === 'Delete' && selectedElementsCount > 0) {
        event.preventDefault()
        handleDeleteAllSelected()
        return
      }

      if (isPrimaryModifier && !event.shiftKey && key === 'a' && nodes.length > 0) {
        event.preventDefault()
        handleSelectAll()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleSaveClick, handleSelectAll, handleDeleteAllSelected, nodes.length, selectedElementsCount])

  const saveActionsDisabled = saving || nodes.length === 0

  return (
    <BuilderContainer ref={builderRootRef}>
      {/* Unified Toolbar */}
      <Toolbar>
        {/* File Menu */}
        <MenuTrigger
          type="button"
          aria-haspopup="menu"
          aria-expanded={Boolean(fileMenuAnchor)}
          onClick={handleFileMenuOpen}
        >
          File
        </MenuTrigger>
        <StyledMenu
          anchorEl={fileMenuAnchor}
          open={Boolean(fileMenuAnchor)}
          onClose={handleFileMenuClose}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <StyledMenuItem onClick={() => { handleFileMenuClose(); handleNewFlow(); }}>
            <span>New Flow</span>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleOpenDialogOpen}>
            <span>Open Flow...</span>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleManageDialogOpen}>
            <span>Manage Flows...</span>
          </StyledMenuItem>
          <Divider sx={{ my: 0.5 }} />
          <StyledMenuItem onClick={handleSaveClick} disabled={saveActionsDisabled}>
            <span>{saving ? 'Saving...' : 'Save'}</span>
            <Shortcut>{primaryShortcutLabel}+S</Shortcut>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleSaveAsClick} disabled={saveActionsDisabled}>
            <span>Save As...</span>
          </StyledMenuItem>
          <Divider sx={{ my: 0.5 }} />
          <StyledMenuItem onClick={handleDeleteFlowClick} disabled={!currentFlowId}>
            <Typography sx={{ color: currentFlowId ? 'error.main' : 'inherit', fontSize: '0.8rem' }}>
              Delete Flow
            </Typography>
          </StyledMenuItem>
        </StyledMenu>

        {/* Edit Menu */}
        <MenuTrigger
          type="button"
          aria-haspopup="menu"
          aria-expanded={Boolean(editMenuAnchor)}
          onClick={handleEditMenuOpen}
        >
          Edit
        </MenuTrigger>
        <StyledMenu
          anchorEl={editMenuAnchor}
          open={Boolean(editMenuAnchor)}
          onClose={handleEditMenuClose}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <StyledMenuItem onClick={handleSelectAll} disabled={nodes.length === 0}>
            <span>Select All</span>
            <Shortcut>{primaryShortcutLabel}+A</Shortcut>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleDeleteAllSelected} disabled={selectedElementsCount === 0}>
            <Typography sx={{ color: selectedElementsCount > 0 ? 'error.main' : 'inherit', fontSize: '0.8rem' }}>
              Delete Selected {selectedElementsCount > 0 && `(${selectedElementsCount})`}
            </Typography>
            <Shortcut>Del</Shortcut>
          </StyledMenuItem>
        </StyledMenu>

        <FileActionStrip role="toolbar" aria-label="File actions">
          <Tooltip title="New flow">
            <span>
              <FileActionButton
                aria-label="New flow"
                size="small"
                onClick={handleNewFlow}
              >
                <NoteAddIcon />
              </FileActionButton>
            </span>
          </Tooltip>
          <Tooltip title="Open flow">
            <span>
              <FileActionButton
                aria-label="Open flow"
                size="small"
                onClick={handleOpenDialogOpen}
              >
                <FolderOpenIcon />
              </FileActionButton>
            </span>
          </Tooltip>
          <Tooltip title="Manage flows">
            <span>
              <FileActionButton
                aria-label="Manage flows"
                size="small"
                onClick={handleManageDialogOpen}
              >
                <ListAltIcon />
              </FileActionButton>
            </span>
          </Tooltip>
          <Tooltip title={saving ? 'Saving...' : 'Save flow'}>
            <span>
              <FileActionButton
                aria-label="Save flow"
                size="small"
                onClick={handleSaveClick}
                disabled={saveActionsDisabled}
              >
                <SaveIcon />
              </FileActionButton>
            </span>
          </Tooltip>
          <Tooltip title="Save flow as">
            <span>
              <FileActionButton
                aria-label="Save flow as"
                size="small"
                onClick={handleSaveAsClick}
                disabled={saveActionsDisabled}
              >
                <SaveAsIcon />
              </FileActionButton>
            </span>
          </Tooltip>
        </FileActionStrip>

        {/* Verify with Claude Button */}
        {onVerifyRequest && nodes.length > 0 && (
          <Button
            onClick={onVerifyRequest}
            size="small"
            startIcon={<AutoFixHighIcon sx={{ fontSize: 14 }} />}
            sx={{
              ml: 1,
              px: 1,
              py: 0.25,
              minHeight: 'auto',
              fontSize: '0.75rem',
              fontWeight: 500,
              textTransform: 'none',
              color: 'primary.main',
              backgroundColor: 'transparent',
              '&:hover': {
                backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.08),
              },
            }}
          >
            Verify with Claude
          </Button>
        )}

        {/* Status Area */}
        <ToolbarStatus>
          <DescriptionOutlinedIcon sx={{ fontSize: 14 }} />
          <span>{currentFlowId ? flowName : 'Untitled'}</span>
          {nodes.length > 0 && (
            <>
              <FiberManualRecordIcon sx={{ fontSize: 4, opacity: 0.5 }} />
              <span>{nodes.length} step{nodes.length !== 1 ? 's' : ''}</span>
            </>
          )}
        </ToolbarStatus>
      </Toolbar>

      <BuilderContent>
        <PanelGroup
          direction="horizontal"
          autoSaveId="flow-builder-palette"
          style={{ width: '100%', height: '100%' }}
        >
          <Panel defaultSize={28} minSize={15} maxSize={40}>
            <SidePanel>
              <AgentPalette
                isCollapsed={paletteCollapsed}
                onToggleCollapse={() => setPaletteCollapsed(!paletteCollapsed)}
              />

              <Paper sx={{ p: 1.5, flexShrink: 0 }} elevation={1}>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  Tip: Drag agents from palette to canvas, then connect them.
                </Typography>
              </Paper>
            </SidePanel>
          </Panel>

          <ResizeHandle />

          <Panel defaultSize={72} minSize={60}>
            <CanvasArea
              ref={reactFlowWrapper}
              role="region"
              aria-label="Flow canvas"
              tabIndex={0}
              onMouseDown={() => reactFlowWrapper.current?.focus()}
            >
              {loading ? (
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                  <CircularProgress />
                </Box>
              ) : (
                <ReactFlow
                  nodes={canvasNodes}
                  edges={canvasEdges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  onInit={setReactFlowInstance}
                  onNodeClick={onNodeClick}
                  onPaneClick={onPaneClick}
                  onDragOver={onDragOver}
                  onDrop={onDrop}
                  nodeTypes={nodeTypes}
                  edgeTypes={edgeTypes}
                  snapToGrid
                  snapGrid={[16, 16]}
                  defaultViewport={{ x: 0, y: 0, zoom: 1 }}
                  defaultEdgeOptions={{
                    type: 'deletable',
                    animated: true,
                    style: { strokeWidth: 2 },
                  }}
                >
                  <Controls showInteractive={false} />
                  <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
                </ReactFlow>
              )}

              {/* Node Editor Panel - Show different editors based on node type */}
              {selectedEditorNode && (selectedEditorNode.type === 'task_input' || selectedEditorNode.data.agent_id === 'task_input') ? (
                <TaskInputEditor
                  node={selectedEditorNode}
                  onSave={handleNodeDataUpdate}
                  onClose={() => setSelectedNode(null)}
                  onTaskInstructionsAuthored={() => setTaskInstructionsDefaultOnly(false)}
                  onDelete={handleDeleteNode}
                />
              ) : selectedEditorNode ? (
                <NodeEditor
                  node={selectedEditorNode}
                  outputBinding={selectedOutputBinding}
                  onSave={handleNodeDataUpdate}
                  onClose={() => setSelectedNode(null)}
                  onDelete={handleDeleteNode}
                  onViewPrompts={handleViewPrompts}
                  onViewDomainEnvelope={handleViewDomainEnvelope}
                />
              ) : null}

              {/* Domain Envelope Slide-over */}
              {domainEnvelopeViewerNode && domainEnvelopeViewerMetadata && (
                <DomainEnvelopeViewer
                  agentName={domainEnvelopeViewerNode.data.agent_display_name}
                  metadata={domainEnvelopeViewerMetadata}
                  validationAttachments={domainEnvelopeViewerNode.data.validation_attachments || []}
                  open={domainEnvelopeViewerOpen}
                  onClose={handleCloseDomainEnvelopeViewer}
                />
              )}

              {/* Prompt Viewer Slide-over */}
              {promptViewerAgent && (
                <PromptViewer
                  agentId={promptViewerAgent.id}
                  agentName={promptViewerAgent.name}
                  open={promptViewerOpen}
                  onClose={handleClosePromptViewer}
                />
              )}
            </CanvasArea>
          </Panel>
        </PanelGroup>
      </BuilderContent>

      <Dialog
        open={Boolean(bindingDialog)}
        onClose={handleBindingDialogClose}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
            Choose Validator Binding
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.35 }}>
            This custom validator must name the domain-pack binding it satisfies.
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ pt: 1 }}>
          <List dense disablePadding>
            {bindingDialog?.bindings.map((binding) => (
              <ListItem key={binding.attachment_id} disablePadding sx={{ mb: 0.75 }}>
                <ListItemButton
                  onClick={() => handleBindingDialogSelect(binding)}
                  sx={{
                    border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.75)}`,
                    borderRadius: 1,
                    alignItems: 'flex-start',
                  }}
                >
                  <ListItemText
                    primary={binding.label}
                    secondary={[
                      validationEdgeLabel(binding),
                      binding.validator_package_id && binding.validator_agent_id
                        ? `${binding.validator_package_id}:${binding.validator_agent_id}`
                        : binding.validator_id,
                      binding.blocking ? 'blocking' : null,
                      binding.required ? 'required' : null,
                    ].filter(Boolean).join(' / ')}
                    primaryTypographyProps={{ fontSize: '0.82rem', fontWeight: 650 }}
                    secondaryTypographyProps={{ fontSize: '0.7rem' }}
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleBindingDialogClose}>Cancel</Button>
        </DialogActions>
      </Dialog>

      {/* Open Flow Dialog */}
      <Dialog
        open={openDialogOpen}
        onClose={handleOpenDialogClose}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2,
            maxHeight: '70vh',
          },
        }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
            Open Flow
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ pt: 1 }}>
          <TextField
            fullWidth
            size="small"
            placeholder="Search flows..."
            value={flowSearchTerm}
            onChange={(e) => setFlowSearchTerm(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                </InputAdornment>
              ),
            }}
            sx={{ mb: 2 }}
          />
          <Box sx={{ minHeight: 200, maxHeight: 300, overflow: 'auto' }}>
            {loadingFlows ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                <CircularProgress size={24} />
              </Box>
            ) : filteredFlows.length === 0 ? (
              <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                <Typography variant="body2">
                  {flowSearchTerm ? 'No flows match your search' : 'No saved flows yet'}
                </Typography>
              </Box>
            ) : (
              <List disablePadding>
                {filteredFlows.map((flow) => (
                  <ListItem key={flow.id} disablePadding>
                    <ListItemButton
                      onClick={() => handleSelectFlow(flow.id)}
                      selected={flow.id === currentFlowId}
                      sx={{
                        borderRadius: 1,
                        mb: 0.5,
                        '&.Mui-selected': {
                          backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.12),
                        },
                      }}
                    >
                      <DescriptionOutlinedIcon sx={{ fontSize: 18, mr: 1.5, color: 'text.secondary' }} />
                      <ListItemText
                        primary={flow.name}
                        secondary={`${flow.step_count} step${flow.step_count !== 1 ? 's' : ''}`}
                        primaryTypographyProps={{ fontSize: '0.85rem' }}
                        secondaryTypographyProps={{ fontSize: '0.7rem' }}
                      />
                    </ListItemButton>
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={handleOpenDialogClose} size="small">
            Cancel
          </Button>
        </DialogActions>
      </Dialog>

      {/* Save/Save As Flow Dialog */}
      <Dialog
        open={saveDialogOpen}
        onClose={handleSaveDialogClose}
        maxWidth="xs"
        fullWidth
        PaperProps={{ sx: { borderRadius: 2 } }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
            {saveDialogMode === 'save_as' ? 'Save Flow As' : 'Save Flow'}
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {saveDialogMode === 'save_as'
              ? 'Enter a name for the new copy of this flow'
              : 'Enter a name for your flow'}
          </Typography>
          <TextField
            fullWidth
            size="small"
            placeholder="Flow name"
            value={saveDialogName}
            onChange={(e) => setSaveDialogName(e.target.value)}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleSaveDialogConfirm()
              }
            }}
          />
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={handleSaveDialogClose} size="small">
            Cancel
          </Button>
          <Button
            onClick={handleSaveDialogConfirm}
            variant="contained"
            size="small"
            disabled={!saveDialogName.trim() || saving}
          >
            {saving
              ? 'Saving...'
              : (saveDialogMode === 'save_as' ? 'Save As' : 'Save')}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Flow Confirmation Dialog */}
      <Dialog
        open={deleteConfirmOpen}
        onClose={handleDeleteFlowCancel}
        PaperProps={{ sx: { minWidth: 320, borderRadius: 2 } }}
      >
        <DialogTitle sx={{ fontSize: '1rem' }}>Delete Flow?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            Are you sure you want to delete &ldquo;{flowName}&rdquo;? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={handleDeleteFlowCancel} disabled={deleting} size="small">
            Cancel
          </Button>
          <Button
            onClick={handleDeleteFlowConfirm}
            color="error"
            variant="contained"
            disabled={deleting}
            size="small"
          >
            {deleting ? 'Deleting...' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Manage Flows Dialog */}
      <Dialog
        open={manageDialogOpen}
        onClose={handleManageDialogClose}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2,
            maxHeight: '70vh',
          },
        }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
            Manage Flows
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Rename or delete your saved flows
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ pt: 1 }}>
          <Box sx={{ minHeight: 200, maxHeight: 350, overflow: 'auto' }}>
            {loadingManageFlows ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                <CircularProgress size={24} />
              </Box>
            ) : manageFlows.length === 0 ? (
              <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                <Typography variant="body2">No saved flows yet</Typography>
              </Box>
            ) : (
              <List disablePadding>
                {manageFlows.map((flow) => (
                  <ListItem
                    key={flow.id}
                    disablePadding
                    sx={{
                      mb: 0.5,
                      border: (theme) => `1px solid ${theme.palette.divider}`,
                      borderRadius: 1,
                      backgroundColor: flow.id === currentFlowId
                        ? (theme) => alpha(theme.palette.primary.main, 0.08)
                        : 'transparent',
                    }}
                  >
                    {editingFlowId === flow.id ? (
                      // Edit mode: show text field and save/cancel buttons
                      <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', p: 1, gap: 1 }}>
                        <TextField
                          fullWidth
                          size="small"
                          value={editingFlowName}
                          onChange={(e) => setEditingFlowName(e.target.value)}
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleRenameConfirm()
                            if (e.key === 'Escape') handleRenameCancel()
                          }}
                          sx={{ flex: 1 }}
                        />
                        <Tooltip title="Save">
                          <IconButton
                            size="small"
                            onClick={handleRenameConfirm}
                            disabled={!editingFlowName.trim() || renamingFlow}
                            color="primary"
                          >
                            {renamingFlow ? <CircularProgress size={16} /> : <CheckIcon fontSize="small" />}
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Cancel">
                          <IconButton
                            size="small"
                            onClick={handleRenameCancel}
                            disabled={renamingFlow}
                          >
                            <CloseIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    ) : (
                      // View mode: show name and edit/delete buttons
                      <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', py: 0.5, px: 1 }}>
                        <DescriptionOutlinedIcon sx={{ fontSize: 18, mr: 1.5, color: 'text.secondary' }} />
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Typography
                            variant="body2"
                            sx={{
                              fontSize: '0.85rem',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {flow.name}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {flow.step_count} step{flow.step_count !== 1 ? 's' : ''}
                            {flow.id === currentFlowId && ' • Currently open'}
                          </Typography>
                        </Box>
                        <Tooltip title="Rename">
                          <IconButton
                            size="small"
                            onClick={() => handleRenameStart(flow)}
                            sx={{ ml: 1 }}
                          >
                            <EditIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete">
                          <IconButton
                            size="small"
                            onClick={() => handleDeleteFromManageClick(flow)}
                            sx={{ color: 'error.main' }}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    )}
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={handleManageDialogClose} size="small">
            Close
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Flow from Manage Dialog Confirmation */}
      <Dialog
        open={deleteManageConfirmOpen}
        onClose={handleDeleteFromManageCancel}
        PaperProps={{ sx: { minWidth: 320, borderRadius: 2 } }}
      >
        <DialogTitle sx={{ fontSize: '1rem' }}>Delete Flow?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            Are you sure you want to delete &ldquo;{flowToDeleteFromManage?.name}&rdquo;? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={handleDeleteFromManageCancel} disabled={deletingFromManage} size="small">
            Cancel
          </Button>
          <Button
            onClick={handleDeleteFromManageConfirm}
            color="error"
            variant="contained"
            disabled={deletingFromManage}
            size="small"
          >
            {deletingFromManage ? 'Deleting...' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar for notifications */}
      {snackbar && (
        <Snackbar
          open={true}
          autoHideDuration={4000}
          onClose={() => setSnackbar(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        >
          <Alert severity={snackbar.severity} onClose={() => setSnackbar(null)}>
            {snackbar.message}
          </Alert>
        </Snackbar>
      )}
    </BuilderContainer>
  )
}

// Wrap with ReactFlowProvider
function FlowBuilder(props: FlowBuilderProps) {
  return (
    <ReactFlowProvider>
      <FlowBuilderInner {...props} />
    </ReactFlowProvider>
  )
}

export default FlowBuilder
