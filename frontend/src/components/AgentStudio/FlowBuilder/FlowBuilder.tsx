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
import type {
  FlowBuilderProps,
  FlowState,
  AgentNode,
  AgentNodeData,
  FlowResponse,
  FlowDefinition,
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
import {
  isExtractionAgent,
  isValidationAgent,
  findNearestExtractor,
  validatorNeedsConfiguration,
} from './smartDefaultUtils'

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
    input_source: 'user_query',
    output_key: 'task_input',
  },
})

/**
 * Pure function to compute validation errors for all nodes.
 * Takes nodes/edges as arguments to avoid closure over React state.
 * Returns array of { nodeId, message } for nodes with configuration errors.
 */
const computeValidationErrors = (
  currentNodes: AgentNode[],
  currentEdges: { source: string; target: string }[]
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

  // Check all validators for ambiguous input sources
  for (const node of currentNodes) {
    if (isValidationAgent(node.data.agent_id)) {
      const result = validatorNeedsConfiguration(node.id, currentNodes, currentEdges)
      if (result.needsConfig) {
        errors.push({
          nodeId: node.id,
          message: result.reason || 'Configuration required',
        })
      }
    }
  }

  return errors
}

/**
 * Tri-state for tracking how a node's input was configured:
 * - 'auto': System applied smart defaults (e.g., validator pointing to extractor)
 * - 'manual': User explicitly saved configuration in NodeEditor
 * - 'unset': Node dropped but no smart default applied (user hasn't touched it)
 */
type InputConfigState = 'auto' | 'manual' | 'unset'

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
const MenuTrigger = styled(Box)(({ theme }) => ({
  display: 'inline-flex',
  alignItems: 'center',
  padding: theme.spacing(0.25, 1),
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
  task_input: FlowNode,  // Uses same component with conditional styling
}

// Custom edge types for React Flow - deletable edges with X button on hover
const edgeTypes = {
  deletable: DeletableEdge,
}

function FlowBuilderInner({ flowId, onFlowSaved, onFlowChange, onVerifyRequest }: FlowBuilderProps) {
  // React Flow state
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<AgentNodeData>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])

  // Node ID counter - useRef to persist across renders without causing HMR issues
  const nodeIdRef = useRef(0)
  const getNodeId = useCallback(() => `node_${nodeIdRef.current++}`, [])

  // UI state
  const [flowName, setFlowName] = useState('New Flow')
  const [flowDescription, setFlowDescription] = useState('')
  const [selectedNode, setSelectedNode] = useState<AgentNode | null>(null)
  const [paletteCollapsed, setPaletteCollapsed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [snackbar, setSnackbar] = useState<{ message: string; severity: 'success' | 'error' } | null>(null)

  // Prompt viewer state
  const [promptViewerOpen, setPromptViewerOpen] = useState(false)
  const [promptViewerAgent, setPromptViewerAgent] = useState<{ id: string; name: string } | null>(null)

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

  // Save Dialog state (for new flows)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [saveDialogName, setSaveDialogName] = useState('')

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

  // Track how each node's input was configured (smart defaults feature)
  // - 'auto': System applied smart defaults (preserve on connection)
  // - 'manual': User explicitly saved in NodeEditor (never overwrite)
  // - 'unset': No smart default applied, user hasn't touched it (can auto-switch)
  const [inputConfigState, setInputConfigState] = useState<Record<string, InputConfigState>>({})

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
      setFlowName(flow.name)
      setFlowDescription(flow.description || '')
      setCurrentFlowId(flow.id)

      // Convert flow definition to React Flow format
      const flowNodes = flow.flow_definition.nodes.map((n) => ({
        id: n.id,
        type: n.type === 'task_input' ? 'task_input' : 'agent',
        position: n.position,
        data: n.data,
      }))
      const flowEdges = flow.flow_definition.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'deletable',
        animated: true,
      }))

      setNodes(flowNodes)
      setEdges(flowEdges)

      // Clear config state for loaded flows (absence = treated as 'unset')
      setInputConfigState({})

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
  }, [setNodes, setEdges, reactFlowInstance])

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
        nodes: nodes.map((n) => ({
          id: n.id,
          agent_id: n.data.agent_id,
          agent_display_name: n.data.agent_display_name,
          task_instructions: n.data.task_instructions,
          custom_instructions: n.data.custom_instructions,
          input_source: n.data.input_source,
          custom_input: n.data.custom_input,
          output_key: n.data.output_key,
        })),
        edges: edges.map((e) => ({
          source: e.source,
          target: e.target,
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
      const edgeData = edges.map(e => ({ source: e.source, target: e.target }))
      const errors = computeValidationErrors(currentNodes as AgentNode[], edgeData)
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
  }, [setNodes, edges]) // Only depends on setNodes and edges

  // Keep ref in sync for use in loadFlow (which is defined before this callback)
  useEffect(() => {
    revalidateValidatorsRef.current = revalidateValidators
  }, [revalidateValidators])

  // Stable key for edge topology - triggers revalidation when edges are rewired (not just added/removed)
  // Includes handles to catch rewires between different connection points on the same nodes
  const edgeTopologyKey = useMemo(
    () => edges.map(e => `${e.source}:${e.sourceHandle ?? ''}-${e.target}:${e.targetHandle ?? ''}`).sort().join(','),
    [edges]
  )

  // Re-validate when nodes or edges change (extractors added/removed, connections changed/rewired)
  useEffect(() => {
    // Debounce to avoid excessive re-validation during rapid changes
    const timeoutId = setTimeout(revalidateValidators, 100)
    return () => clearTimeout(timeoutId)
  }, [nodes.length, edgeTopologyKey]) // eslint-disable-line react-hooks/exhaustive-deps
  // Note: We deliberately exclude revalidateValidators to avoid re-render loops

  // Save flow to API (nameOverride allows passing name directly for new flows)
  const handleSave = async (nameOverride?: string) => {
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

    // Check for parallel/branching flows (not yet supported)
    // Count outgoing edges per node - if any node has more than one, it's a parallel flow
    const outgoingEdgeCounts = new Map<string, number>()
    edges.forEach(e => {
      outgoingEdgeCounts.set(e.source, (outgoingEdgeCounts.get(e.source) || 0) + 1)
    })
    const parallelNode = nodes.find(n => (outgoingEdgeCounts.get(n.id) || 0) > 1)
    if (parallelNode) {
      setSelectedNode(parallelNode as AgentNode)
      setSnackbar({
        message: `Parallel flows not yet supported. "${parallelNode.data.agent_display_name}" has multiple outgoing connections. This feature will be available in a future update.`,
        severity: 'error'
      })
      return
    }

    // Compute validation errors fresh (don't rely on stale hasError state)
    const edgeData = edges.map(e => ({ source: e.source, target: e.target }))
    const validationErrors = computeValidationErrors(nodes as AgentNode[], edgeData)
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
        version: '1.0',
        nodes: nodes.map((n) => ({
          id: n.id,
          type: n.data.agent_id === 'task_input' ? 'task_input' : 'agent',
          position: n.position,
          data: n.data,
        })),
        edges: edges.map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
        })),
        entry_node_id: entryNodeId,
      }

      let savedFlow: FlowResponse
      if (currentFlowId) {
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

      // Update flowName state to match saved name
      setFlowName(nameToUse)
      setSnackbar({ message: 'Flow saved successfully', severity: 'success' })
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
    setCurrentFlowId(null)
    setSelectedNode(null)
    setInputConfigState({})  // Clear config state for new flows
    nodeIdRef.current = 1  // Start from 1 since node_0 is used
  }

  // Handle connection between nodes
  const onConnect = useCallback(
    (params: Connection) => {
      // Prevent connections TO task_input nodes (they can only have outgoing connections)
      if (params.target) {
        const targetNode = nodes.find((n) => n.id === params.target)
        if (targetNode?.data.agent_id === 'task_input') {
          setSnackbar({ message: 'Initial Instructions node cannot have incoming connections', severity: 'error' })
          return
        }
      }

      setEdges((eds) => addEdge({ ...params, animated: true }, eds))

      // Auto-switch target node's input_source to 'previous_output' when connected
      // Skip for:
      // 1. Extraction agents (fixed input - always uses PDF document)
      // 2. Nodes with 'auto' state (preserve smart default extractor reference)
      // 3. Nodes with 'manual' state (respect user's explicit choice)
      // Only auto-switch for 'unset' nodes (absence in map = 'unset')
      if (params.target) {
        const targetNode = nodes.find((n) => n.id === params.target)
        if (targetNode) {
          const isExtractor = isExtractionAgent(targetNode.data.agent_id)
          const configState = inputConfigState[params.target] // undefined = 'unset'
          const shouldSkipAutoSwitch = isExtractor || configState === 'auto' || configState === 'manual'

          if (!shouldSkipAutoSwitch) {
            setNodes((nds) =>
              nds.map((node) =>
                node.id === params.target
                  ? { ...node, data: { ...node.data, input_source: 'previous_output' } }
                  : node
              )
            )
          }
        }
      }
    },
    [setEdges, setNodes, nodes, inputConfigState]
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
        const { type, agentId, agentName, agentDescription } = JSON.parse(data)
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

        const newNodeId = getNodeId()

        // Determine smart defaults for input_source
        let inputSource: 'user_query' | 'previous_output' | 'custom' = 'custom'
        let customInput: string | undefined = undefined
        let shouldMarkAutoConfigured = false

        if (isTaskInput) {
          inputSource = 'user_query'
        } else if (agentId === 'pdf') {
          inputSource = 'previous_output'
        } else if (isValidationAgent(agentId)) {
          // Smart default: Validators should use extractor output, not previous validator output
          const extractor = findNearestExtractor(newNodeId, nodes as AgentNode[], edges)
          if (extractor) {
            inputSource = 'custom'
            customInput = `{{${extractor.data.output_key}}}`
            shouldMarkAutoConfigured = true
          }
        }

        const newNode: AgentNode = {
          id: newNodeId,
          type: isTaskInput ? 'task_input' : 'agent',
          position,
          data: {
            agent_id: agentId,
            agent_display_name: agentName,
            agent_description: agentDescription,
            task_instructions: isTaskInput ? '' : undefined,
            custom_instructions: '',
            input_source: inputSource,
            custom_input: customInput,
            output_key: isTaskInput ? 'task_input' : `${agentId}_output`,
          },
        }

        setNodes((nds) => [...nds, newNode])

        // Set config state based on whether smart defaults were applied
        if (shouldMarkAutoConfigured) {
          setInputConfigState(prev => ({ ...prev, [newNodeId]: 'auto' }))
        } else if (!isTaskInput && !isExtractionAgent(agentId)) {
          // Non-special agents without smart defaults start as 'unset'
          setInputConfigState(prev => ({ ...prev, [newNodeId]: 'unset' }))
        }
      } catch (err) {
        logger.error('Failed to parse drag data', err as Error, { component: 'FlowBuilder' })
      }
    },
    [reactFlowInstance, setNodes, getNodeId, nodes, edges]
  )

  // Handle node data update from editor
  const handleNodeDataUpdate = useCallback(
    (nodeId: string, data: Partial<AgentNodeData>) => {
      setNodes((nds) =>
        nds.map((node) =>
          node.id === nodeId
            ? { ...node, data: { ...node.data, ...data } }
            : node
        )
      )
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
    [setNodes, revalidateValidators]
  )

  // Handle node deletion by ID (for NodeEditor delete button)
  const handleDeleteNode = useCallback((nodeId: string) => {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId))
    setEdges((eds) =>
      eds.filter((e) => e.source !== nodeId && e.target !== nodeId)
    )
    setSelectedNode(null)
    // Clean up config state for deleted node (prevents memory leak)
    setInputConfigState(prev => {
      const { [nodeId]: _, ...rest } = prev
      return rest
    })
  }, [setNodes, setEdges])

  // Get available output variables from nodes before the selected node
  const availableVariables = useMemo(() => {
    if (!selectedNode) return []
    return nodes
      .filter((n) => n.id !== selectedNode.id && n.data.output_key && n.data.output_key.length > 0)
      .map((n) => n.data.output_key)
  }, [nodes, selectedNode])

  // Check if selected node has an incoming edge
  const hasIncomingEdge = useMemo(() => {
    if (!selectedNode) return false
    return edges.some((e) => e.target === selectedNode.id)
  }, [edges, selectedNode])

  // Handle marking a node as manually configured when user saves in NodeEditor
  // This prevents auto-switching on future connections
  const handleMarkManuallyConfigured = useCallback((nodeId: string) => {
    setInputConfigState(prev => ({ ...prev, [nodeId]: 'manual' }))
  }, [])

  // Handle opening prompt viewer
  const handleViewPrompts = useCallback((agentId: string, agentName: string) => {
    setPromptViewerAgent({ id: agentId, name: agentName })
    setPromptViewerOpen(true)
  }, [])

  // Handle closing prompt viewer
  const handleClosePromptViewer = useCallback(() => {
    setPromptViewerOpen(false)
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
    listFlows()
      .then((response) => setSavedFlows(response.flows))
      .catch((err) => {
        logger.error('Failed to load flows', err as Error, { component: 'FlowBuilder' })
        setSnackbar({ message: 'Failed to load flows', severity: 'error' })
      })
      .finally(() => setLoadingFlows(false))
  }, [])

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
      setSaveDialogName(flowName === 'New Flow' ? '' : flowName)
      setSaveDialogOpen(true)
    }
  }, [currentFlowId, flowName, nodes.length, handleSave])

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
    handleSave(nameToSave)
  }, [saveDialogName, handleSave])

  // Manage Flows Dialog handlers
  const handleManageDialogOpen = useCallback(() => {
    setFileMenuAnchor(null)
    setManageDialogOpen(true)
    setEditingFlowId(null)
    setEditingFlowName('')
    // Load flows
    setLoadingManageFlows(true)
    listFlows()
      .then((response) => setManageFlows(response.flows))
      .catch((err) => {
        logger.error('Failed to load flows', err as Error, { component: 'FlowBuilder' })
        setSnackbar({ message: 'Failed to load flows', severity: 'error' })
      })
      .finally(() => setLoadingManageFlows(false))
  }, [])

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
      // Update with new name
      await updateFlow(editingFlowId, {
        name: editingFlowName.trim(),
        description: fullFlow.description || undefined,
        flow_definition: fullFlow.flow_definition,
      })
      // Update local list
      setManageFlows((flows) =>
        flows.map((f) => (f.id === editingFlowId ? { ...f, name: editingFlowName.trim() } : f))
      )
      // If this is the currently loaded flow, update the flowName state too
      if (editingFlowId === currentFlowId) {
        setFlowName(editingFlowName.trim())
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
  }, [editingFlowId, editingFlowName, currentFlowId])

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
    // React Flow handles selection via onNodesChange, but we can select all by updating selection state
    setNodes((nds) => nds.map((n) => ({ ...n, selected: true })))
  }, [setNodes])

  // Handle delete all selected nodes (for Edit menu)
  const handleDeleteAllSelected = useCallback(() => {
    setEditMenuAnchor(null)
    // Get all selected nodes
    const selectedNodeIds = nodes.filter((n) => n.selected).map((n) => n.id)
    if (selectedNodeIds.length === 0) {
      setSnackbar({ message: 'No nodes selected', severity: 'error' })
      return
    }
    // Remove selected nodes and their edges
    setNodes((nds) => nds.filter((n) => !n.selected))
    setEdges((eds) => eds.filter((e) => !selectedNodeIds.includes(e.source) && !selectedNodeIds.includes(e.target)))
    setSelectedNode(null)
    // Clean up config state for deleted nodes (prevents memory leak)
    setInputConfigState(prev => {
      const next = { ...prev }
      selectedNodeIds.forEach(id => delete next[id])
      return next
    })
  }, [nodes, setNodes, setEdges])

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

  // Count selected nodes for Edit menu
  const selectedNodesCount = nodes.filter((n) => n.selected).length

  return (
    <BuilderContainer>
      {/* Unified Toolbar */}
      <Toolbar>
        {/* File Menu */}
        <MenuTrigger onClick={handleFileMenuOpen}>File</MenuTrigger>
        <StyledMenu
          anchorEl={fileMenuAnchor}
          open={Boolean(fileMenuAnchor)}
          onClose={handleFileMenuClose}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <StyledMenuItem onClick={() => { handleFileMenuClose(); handleNewFlow(); }}>
            <span>New Flow</span>
            <Shortcut>Ctrl+N</Shortcut>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleOpenDialogOpen}>
            <span>Open Flow...</span>
            <Shortcut>Ctrl+O</Shortcut>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleManageDialogOpen}>
            <span>Manage Flows...</span>
          </StyledMenuItem>
          <Divider sx={{ my: 0.5 }} />
          <StyledMenuItem onClick={handleSaveClick} disabled={saving || nodes.length === 0}>
            <span>{saving ? 'Saving...' : 'Save'}</span>
            <Shortcut>Ctrl+S</Shortcut>
          </StyledMenuItem>
          <Divider sx={{ my: 0.5 }} />
          <StyledMenuItem onClick={handleDeleteFlowClick} disabled={!currentFlowId}>
            <Typography sx={{ color: currentFlowId ? 'error.main' : 'inherit', fontSize: '0.8rem' }}>
              Delete Flow
            </Typography>
          </StyledMenuItem>
        </StyledMenu>

        {/* Edit Menu */}
        <MenuTrigger onClick={handleEditMenuOpen}>Edit</MenuTrigger>
        <StyledMenu
          anchorEl={editMenuAnchor}
          open={Boolean(editMenuAnchor)}
          onClose={handleEditMenuClose}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <StyledMenuItem onClick={handleSelectAll} disabled={nodes.length === 0}>
            <span>Select All</span>
            <Shortcut>Ctrl+A</Shortcut>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleDeleteAllSelected} disabled={selectedNodesCount === 0}>
            <Typography sx={{ color: selectedNodesCount > 0 ? 'error.main' : 'inherit', fontSize: '0.8rem' }}>
              Delete Selected {selectedNodesCount > 0 && `(${selectedNodesCount})`}
            </Typography>
            <Shortcut>Del</Shortcut>
          </StyledMenuItem>
        </StyledMenu>

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
            <CanvasArea ref={reactFlowWrapper}>
              {loading ? (
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                  <CircularProgress />
                </Box>
              ) : (
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
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
              {selectedNode && (selectedNode.type === 'task_input' || selectedNode.data.agent_id === 'task_input') ? (
                <TaskInputEditor
                  node={selectedNode}
                  onSave={handleNodeDataUpdate}
                  onClose={() => setSelectedNode(null)}
                  onDelete={handleDeleteNode}
                />
              ) : selectedNode ? (
                <NodeEditor
                  node={selectedNode}
                  onSave={handleNodeDataUpdate}
                  onClose={() => setSelectedNode(null)}
                  onDelete={handleDeleteNode}
                  availableVariables={availableVariables}
                  onViewPrompts={handleViewPrompts}
                  hasIncomingEdge={hasIncomingEdge}
                  onMarkManuallyConfigured={handleMarkManuallyConfigured}
                />
              ) : null}

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

      {/* Save Flow Dialog (for new flows) */}
      <Dialog
        open={saveDialogOpen}
        onClose={handleSaveDialogClose}
        maxWidth="xs"
        fullWidth
        PaperProps={{ sx: { borderRadius: 2 } }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
            Save Flow
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Enter a name for your flow
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
            {saving ? 'Saving...' : 'Save'}
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
