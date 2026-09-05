/**
 * Agent Studio Page
 *
 * Adaptive shell for exploring agent prompts, building flows, and using AI Chat:
 * - Left: work surface with the Agents, Flows, and Agent Workshop tabs
 * - Right: AI Chat pane (30% by default) that collapses to a 44px rail
 * - Below 1100px: the pane becomes a right-side drawer opened from the tab bar
 *
 * OpusChat stays mounted across collapse, expand, and drawer open/close so
 * streaming, drafts, and the durable session survive every transition.
 *
 * Entry points:
 * 1. Nav bar link to /agent-studio (fresh start)
 * 2. Triple-dot menu "Open in Agent Studio" with trace context
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Box,
  Backdrop,
  Badge,
  Button,
  CircularProgress,
  Alert,
  Typography,
  Stack,
  Tabs,
  Tab,
  useMediaQuery,
} from '@mui/material'
import { styled } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle, type ImperativePanelHandle } from 'react-resizable-panels'
import DescriptionIcon from '@mui/icons-material/Description'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import ScienceIcon from '@mui/icons-material/Science'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'

import OpusChat from '@/components/AgentStudio/OpusChat'
import ClaudeRail, { formatUnreadDescription } from '@/components/AgentStudio/ClaudeRail'
import ClaudeDrawer from '@/components/AgentStudio/ClaudeDrawer'
import { buildFlowVerificationPrompt } from '@/components/AgentStudio/flowVerificationPrompt'
import AgentBrowser from '@/components/AgentStudio/AgentBrowser'
import {
  FlowBuilder,
  type FlowAuthoringContextHandle,
  type FlowState,
} from '@/components/AgentStudio/FlowBuilder'
import type { AgentBrowserRequest, AgentDetailsRequest } from '@/components/AgentStudio/agentBrowserRequest'
import PromptWorkshop, {
  type WorkshopAuthoringContextHandle,
  type WorkshopLeaveGuard,
} from '@/components/AgentStudio/PromptWorkshop/PromptWorkshop'
import { canonicalAuthoringJson, fingerprintAuthoringContext } from '@/components/AgentStudio/authoringContext'
import {
  useChatHistoryDetailQuery,
  useChatHistoryTranscriptQuery,
} from '@/features/history/useChatHistoryQuery'
import { cloneAgentToWorkshop, fetchPromptCatalog, getWorkshopSavedReference } from '@/services/agentStudioService'
import {
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  buildRestorableChatMessages,
} from '@/services/chatHistoryApi'
import { safeGetItem, safeRemoveItem, safeSetItem } from '@/lib/browserStorage'
import logger from '@/services/logger'
import type {
  PromptCatalog,
  ChatContext,
  FlowContextDefinition,
  AgentWorkshopContext,
  ToolIdeaConversationEntry,
  WorkshopAuthoringProposal,
  WorkshopContinuationOrigin,
  WorkshopSavedHandoff,
  FlowAuthoringProposal,
} from '@/types/promptExplorer'

const Root = styled(Box)(({ theme }) => ({
  flex: 1,
  display: 'flex',
  height: '100%',
  overflow: 'hidden',
  padding: theme.spacing(1),
}))

/** Paper card with the shared 1px divider border and 8px radius from the shell mockup. */
const PanelCard = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  // The Panel element is a flex row; without these the card is sized by its
  // content and leaves the rest of the panel empty.
  flex: '1 1 0%',
  width: '100%',
  minWidth: 0,
  minHeight: 0,
  height: '100%',
  backgroundColor: theme.palette.background.paper,
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.shape.borderRadius * 2,
  overflow: 'hidden',
}))

const ClaudePanelSection = styled(PanelCard, {
  shouldForwardProp: (prop) => prop !== 'collapsed',
})<{ collapsed: boolean }>(({ collapsed }) => ({
  visibility: collapsed ? 'hidden' : 'visible',
  '& > *': {
    flex: 1,
    minHeight: 0,
    height: '100%',
  },
}))

const ResizeHandle = styled(PanelResizeHandle, {
  shouldForwardProp: (prop) => prop !== 'collapsed',
})<{ collapsed: boolean }>(({ theme, collapsed }) => ({
  width: 8,
  flex: '0 0 8px',
  display: collapsed ? 'none' : 'block',
  cursor: 'col-resize',
  position: 'relative',
  '&::after': {
    content: '""',
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 3,
    width: 2,
    borderRadius: 1,
    backgroundColor: theme.palette.divider,
    transition: 'background-color 0.2s ease',
  },
  '&:hover::after, &[data-resize-handle-active]::after': {
    backgroundColor: theme.palette.primary.main,
  },
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: -2,
  },
}))

const TabBar = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  minHeight: 40,
  borderBottom: `1px solid ${theme.palette.divider}`,
  paddingRight: theme.spacing(1),
  flex: 'none',
}))

const StyledTabs = styled(Tabs)(() => ({
  minHeight: 40,
  flex: 1,
  minWidth: 0,
  '& .MuiTabs-indicator': {
    height: 3,
  },
}))

const VisuallyHidden = styled('span')({
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
})

const StyledTab = styled(Tab)(({ theme }) => ({
  minHeight: 40,
  textTransform: 'none',
  fontWeight: 500,
  fontSize: '0.85rem',
  '&.Mui-selected': {
    color: theme.palette.primary.main,
  },
}))

const TabContent = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  overflow: 'hidden',
}))

type TabValue = 'agents' | 'flows' | 'agent_workshop'

// localStorage key for tab persistence
const AGENT_STUDIO_TAB_KEY = 'agent-studio-tab'
// Panel split persistence. The key changed with the Layout R shell so the old
// 40/60 split cannot survive; the stale entry is removed once on mount.
const AGENT_STUDIO_PANELS_AUTOSAVE_ID = 'agent-studio-panels-v2'
const STALE_PANELS_STORAGE_KEY = 'react-resizable-panels:agent-studio-panels'
const CLAUDE_COLLAPSED_KEY = 'agent-studio-claude-collapsed'
const CLAUDE_PANEL_ID = 'agent-studio-claude-panel'
const CLAUDE_DRAWER_ID = 'agent-studio-claude-drawer'
const LAUNCHER_UNREAD_DESCRIPTION_ID = 'agent-studio-claude-launcher-unread'
// Below this width the side panel and rail give way to the drawer.
const NARROW_SHELL_QUERY = '(max-width:1099px)'
// Below this width the drawer covers the full viewport.
const FULL_WIDTH_DRAWER_QUERY = '(max-width:719px)'

function readCollapsedPreference(): boolean {
  const result = safeGetItem(() => window.localStorage, CLAUDE_COLLAPSED_KEY, {
    owner: 'preferences',
    key: CLAUDE_COLLAPSED_KEY,
    quiet: true,
  })
  return result.ok && result.value === 'true'
}

function writeCollapsedPreference(collapsed: boolean) {
  safeSetItem(() => window.localStorage, CLAUDE_COLLAPSED_KEY, String(collapsed), {
    owner: 'preferences',
    key: CLAUDE_COLLAPSED_KEY,
  })
}

function normalizeSearchParam(value: string | null): string | null {
  return value?.trim() ? value.trim() : null
}

function buildSeededOpusConversation(messages: Parameters<typeof buildRestorableChatMessages>[0]): ToolIdeaConversationEntry[] {
  return buildRestorableChatMessages(messages, { onUnknownRole: 'throw' }).flatMap((message) => {
    if (message.role === 'flow' || !message.content.trim()) {
      return []
    }

    return [{
      role: message.role,
      content: message.content,
      timestamp: message.timestamp ?? null,
    }]
  })
}

function AgentStudioPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // Data state
  const [catalog, setCatalog] = useState<PromptCatalog | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // UI state (with persistence)
  const [activeTab, setActiveTab] = useState<TabValue>(() => {
    const storedResult = safeGetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, {
      owner: 'preferences',
      key: AGENT_STUDIO_TAB_KEY,
      quiet: true,
    })
    const stored = storedResult.ok ? storedResult.value : null
    // Migrate old 'prompts' value to 'agents' (tab was renamed)
    if (stored === 'prompts') {
      safeSetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, 'agents', {
        owner: 'preferences',
        key: AGENT_STUDIO_TAB_KEY,
      })
      return 'agents'
    }
    return (stored === 'agents' || stored === 'flows' || stored === 'agent_workshop') ? stored : 'agents'
  })

  const workshopLeaveGuardRef = useRef<WorkshopLeaveGuard | null>(null)
  const workshopAuthoringContextRef = useRef<WorkshopAuthoringContextHandle | null>(null)
  const flowAuthoringContextRef = useRef<FlowAuthoringContextHandle | null>(null)
  const [workshopContinuationOrigin, setWorkshopContinuationOrigin] = useState<WorkshopContinuationOrigin>()
  const [workshopSavedHandoff, setWorkshopSavedHandoff] = useState<WorkshopSavedHandoff>()
  const [continuingToFlow, setContinuingToFlow] = useState(false)
  const continuationBusyRef = useRef(false)
  const continuationMountedRef = useRef(true)
  useEffect(() => {
    continuationMountedRef.current = true
    return () => { continuationMountedRef.current = false }
  }, [])
  const previousAuthoringTabRef = useRef(activeTab)

  useEffect(() => {
    if (activeTab === 'flows') setFlowsVisited(true)
  }, [activeTab])

  // Persist tab changes
  const applyTab = useCallback((newValue: TabValue) => {
    setActiveTab(newValue)
    safeSetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, newValue, {
      owner: 'preferences',
      key: AGENT_STUDIO_TAB_KEY,
    })
  }, [])

  // Ask the Workshop before leaving it so unsaved edits are not dropped silently.
  // The Workshop only mounts once the catalog is loaded; without it there is nothing to guard.
  const confirmLeaveWorkshop = useCallback((): Promise<boolean> => {
    const guard = workshopLeaveGuardRef.current
    if (!guard) return Promise.resolve(true)
    return guard.requestLeave()
  }, [])

  const handleTabChange = useCallback((_e: React.SyntheticEvent, newValue: TabValue) => {
    if (activeTab === 'agent_workshop' && newValue !== 'agent_workshop') {
      void confirmLeaveWorkshop().then((leave) => {
        if (leave) applyTab(newValue)
      })
      return
    }
    applyTab(newValue)
  }, [activeTab, applyTab, confirmLeaveWorkshop])
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null)
  // The Flows tab mounts on first visit and then stays mounted (hidden) so an
  // unsaved flow graph survives a trip to the Agent Browser or the Workshop.
  const [flowsVisited, setFlowsVisited] = useState(false)
  // A deep link into the Agent Browser (from the Flow Builder node panel or the Workshop).
  const [agentDetailsRequest, setAgentDetailsRequest] = useState<AgentDetailsRequest | null>(null)
  const agentDetailsRequestCounterRef = useRef(0)
  const [currentFlowId, setCurrentFlowId] = useState<string | null>(null)
  const [agentWorkshopTemplateSource, setAgentWorkshopTemplateSource] = useState<string | null>(null)
  const [agentWorkshopCustomAgentId, setAgentWorkshopCustomAgentId] = useState<string | null>(null)
  const [agentWorkshopContext, setAgentWorkshopContext] = useState<AgentWorkshopContext | null>(null)
  const [flowState, setFlowState] = useState<FlowState | null>(null)
  const [verifyMessage, setVerifyMessage] = useState<string | null>(null)
  const [discussMessage, setDiscussMessage] = useState<string | null>(null)
  const [opusConversation, setOpusConversation] = useState<ToolIdeaConversationEntry[]>([])
  const [pendingUrlSwapSessionId, setPendingUrlSwapSessionId] = useState<string | null>(null)
  const hydratedConversationSessionRef = useRef<string | null>(null)
  const searchParamsRef = useRef(searchParams)

  // Adaptive shell state
  const isNarrow = useMediaQuery(NARROW_SHELL_QUERY)
  const isFullWidthDrawer = useMediaQuery(FULL_WIDTH_DRAWER_QUERY)
  const [claudeCollapsed, setClaudeCollapsed] = useState<boolean>(readCollapsedPreference)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const [isClaudeStreaming, setIsClaudeStreaming] = useState(false)
  const claudePanelRef = useRef<ImperativePanelHandle>(null)
  const chatInputRef = useRef<HTMLTextAreaElement>(null)
  const railButtonRef = useRef<HTMLButtonElement>(null)
  const launcherButtonRef = useRef<HTMLButtonElement>(null)
  const pendingFocusRef = useRef<'input' | 'toggle' | null>(null)
  // The panel reports its initial layout through onExpand/onCollapse during
  // mount. Those callbacks stay disarmed until the persisted flag is applied.
  const panelCallbacksArmedRef = useRef(false)
  // Assistant-message baseline for unread tracking. Null until OpusChat
  // delivers its first snapshot, so a restored transcript never counts.
  const assistantMessageCountRef = useRef<number | null>(null)
  const claudeHidden = isNarrow ? !drawerOpen : claudeCollapsed
  const claudeHiddenRef = useRef(claudeHidden)

  useEffect(() => {
    claudeHiddenRef.current = claudeHidden
  }, [claudeHidden])

  // Drop the pre-Layout-R saved split once so nobody keeps the 40/60 layout.
  useEffect(() => {
    safeRemoveItem(() => window.localStorage, STALE_PANELS_STORAGE_KEY, {
      owner: 'preferences',
      key: STALE_PANELS_STORAGE_KEY,
      quiet: true,
    })
  }, [])

  // Keep the resizable panel in step with the persisted collapsed flag. The
  // panel remounts when the shell switches between desktop and drawer modes.
  useEffect(() => {
    if (isNarrow) {
      panelCallbacksArmedRef.current = false
      return
    }
    const panel = claudePanelRef.current
    if (!panel) {
      return
    }
    if (claudeCollapsed) {
      panel.collapse()
    } else {
      panel.expand()
    }
    panelCallbacksArmedRef.current = true
  }, [isNarrow, claudeCollapsed])

  const showClaude = useCallback(() => {
    if (!claudeHiddenRef.current) {
      // Already visible (for example a discuss request): just move focus.
      chatInputRef.current?.focus()
      return
    }
    pendingFocusRef.current = 'input'
    if (isNarrow) {
      setDrawerOpen(true)
      return
    }
    setClaudeCollapsed(false)
    writeCollapsedPreference(false)
  }, [isNarrow])

  const hideClaude = useCallback(() => {
    pendingFocusRef.current = 'toggle'
    if (isNarrow) {
      setDrawerOpen(false)
      return
    }
    setClaudeCollapsed(true)
    writeCollapsedPreference(true)
  }, [isNarrow])

  const toggleClaude = useCallback(() => {
    if (claudeHidden) {
      showClaude()
    } else {
      hideClaude()
    }
  }, [claudeHidden, showClaude, hideClaude])

  // Drag-to-collapse and drag-to-expand on the resize handle.
  const handlePanelCollapse = useCallback(() => {
    if (!panelCallbacksArmedRef.current) {
      return
    }
    setClaudeCollapsed(true)
    writeCollapsedPreference(true)
  }, [])

  const handlePanelExpand = useCallback(() => {
    if (!panelCallbacksArmedRef.current) {
      return
    }
    setClaudeCollapsed(false)
    writeCollapsedPreference(false)
  }, [])

  // Ctrl+. / Cmd+. toggles AI Chat from anywhere on the page.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '.' || !(event.ctrlKey || event.metaKey) || event.altKey) {
        return
      }
      event.preventDefault()
      toggleClaude()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [toggleClaude])

  // Focus moves to the chat input on show and back to the toggle on hide.
  useEffect(() => {
    const pending = pendingFocusRef.current
    if (!pending) {
      return
    }
    pendingFocusRef.current = null
    if (pending === 'input' && !claudeHidden) {
      chatInputRef.current?.focus()
    } else if (pending === 'toggle' && claudeHidden) {
      const toggle = isNarrow ? launcherButtonRef.current : railButtonRef.current
      toggle?.focus()
    }
  }, [claudeHidden, isNarrow])

  // Unread tracking: assistant messages appended while AI Chat is hidden. The
  // first snapshot only sets the baseline; later growth while hidden counts.
  const handleConversationSnapshotChange = useCallback((messages: ToolIdeaConversationEntry[]) => {
    setOpusConversation(messages)
    const assistantCount = messages.filter((message) => message.role === 'assistant').length
    const previousCount = assistantMessageCountRef.current
    assistantMessageCountRef.current = assistantCount
    if (previousCount === null) {
      return
    }
    if (claudeHiddenRef.current && assistantCount > previousCount) {
      setUnreadCount((current) => current + (assistantCount - previousCount))
    }
  }, [])

  useEffect(() => {
    if (!claudeHidden) {
      setUnreadCount(0)
    }
  }, [claudeHidden])

  useEffect(() => {
    searchParamsRef.current = searchParams
  }, [searchParams])

  // ALL-276: `session_id` is overloaded in Agent Studio. We classify it by the
  // persisted session's `chat_kind` so assistant-chat sessions seed a new Opus
  // conversation, while agent-studio sessions resume the same durable thread.
  const requestedSessionId = normalizeSearchParam(searchParams.get('session_id'))
  const traceId = normalizeSearchParam(searchParams.get('trace_id'))
  const requestedSessionDetailQuery = useChatHistoryDetailQuery(
    {
      sessionId: requestedSessionId ?? '',
      chatKind: 'all',
      messageLimit: 1,
    },
    {
      enabled: Boolean(requestedSessionId),
      placeholderData: undefined,
    },
  )
  // ALL-276: this page uses `session_id` for both assistant-chat seeding and
  // agent-studio resume, so stale detail data must never classify a new URL.
  const requestedSessionDetail = requestedSessionDetailQuery.data?.session.session_id === requestedSessionId
    ? requestedSessionDetailQuery.data
    : null
  const requestedSessionChatKind = requestedSessionDetail?.session.chat_kind ?? null
  const durableTranscriptQuery = useChatHistoryTranscriptQuery(
    {
      sessionId: requestedSessionId ?? '',
      chatKind: requestedSessionChatKind ?? 'all',
    },
    {
      enabled: Boolean(requestedSessionId && requestedSessionChatKind),
    },
  )
  const seededConversation = useMemo(
    () => buildSeededOpusConversation(durableTranscriptQuery.data?.messages ?? []),
    [durableTranscriptQuery.data?.messages],
  )
  const isPendingInternalUrlSwap = pendingUrlSwapSessionId === requestedSessionId
  const durableTranscriptLoading = Boolean(requestedSessionId) && !isPendingInternalUrlSwap && (
    requestedSessionDetailQuery.isLoading
    || (Boolean(requestedSessionChatKind) && durableTranscriptQuery.isLoading)
  )
  const durableTranscriptError = isPendingInternalUrlSwap
    ? null
    : requestedSessionDetailQuery.error ?? durableTranscriptQuery.error ?? null
  const transcriptSourceSessionId =
    requestedSessionId && seededConversation.length > 0 ? requestedSessionId : undefined
  const resumedDurableSessionId =
    requestedSessionChatKind === AGENT_STUDIO_CHAT_HISTORY_KIND
      ? requestedSessionId
      : null
  const effectiveDurableSessionId = pendingUrlSwapSessionId ?? resumedDurableSessionId

  useEffect(() => {
    if (!requestedSessionId || !durableTranscriptQuery.isSuccess) {
      return
    }

    if (hydratedConversationSessionRef.current === requestedSessionId) {
      return
    }

    const shouldPreserveCurrentConversation = pendingUrlSwapSessionId === requestedSessionId

    setOpusConversation((currentConversation) => {
      hydratedConversationSessionRef.current = requestedSessionId

      if (shouldPreserveCurrentConversation && currentConversation.length > 0) {
        return currentConversation
      }

      // The restored transcript replaces the chat. Move the unread baseline to
      // its assistant count so the snapshot OpusChat publishes for it (before
      // or after this effect) does not count as new messages.
      assistantMessageCountRef.current = seededConversation.filter(
        (message) => message.role === 'assistant',
      ).length
      return seededConversation
    })
    setPendingUrlSwapSessionId((currentPendingSessionId) => (
      currentPendingSessionId === requestedSessionId ? null : currentPendingSessionId
    ))
  }, [requestedSessionId, seededConversation, durableTranscriptQuery.isSuccess, pendingUrlSwapSessionId])

  useEffect(() => {
    if (pendingUrlSwapSessionId !== requestedSessionId) {
      return
    }

    if (!requestedSessionDetailQuery.error && !durableTranscriptQuery.error) {
      return
    }

    setPendingUrlSwapSessionId(null)
  }, [
    pendingUrlSwapSessionId,
    requestedSessionId,
    requestedSessionDetailQuery.error,
    durableTranscriptQuery.error,
  ])

  // Load catalog on mount
  // Trace context is not fetched here; the backend injects it into AI Chat context.
  // when the user sends a message. The trace_id is passed via chatContext.
  useEffect(() => {
    async function loadData() {
      setLoading(true)
      setError(null)

      try {
        // Load the catalog
        const catalogData = await fetchPromptCatalog()
        setCatalog(catalogData)
      } catch (err) {
        console.error('Failed to load prompt explorer data:', err)
        setError(err instanceof Error ? err.message : 'Failed to load data')
      } finally {
        setLoading(false)
      }
    }

    loadData()
  }, [])

  const workshopSelectedAgentId = agentWorkshopContext?.custom_agent_id || agentWorkshopContext?.template_source
  const workshopSelectedGroupId = agentWorkshopContext?.selected_group_id

  const effectiveSelectedAgentId =
    activeTab === 'agent_workshop' ? workshopSelectedAgentId : (selectedAgentId || undefined)
  const effectiveSelectedGroupId =
    activeTab === 'agent_workshop' ? workshopSelectedGroupId : (selectedGroupId || undefined)
  const effectiveViewMode = effectiveSelectedGroupId ? 'combined' : 'base'
  const flowDefinition: FlowContextDefinition | undefined =
    flowsVisited && flowState
      ? {
          version: flowState.version,
          task_instructions_default_only: flowState.task_instructions_default_only,
          entry_node_id: flowState.entry_node_id,
          nodes: flowState.nodes.map((node) => ({
            id: node.id,
            node_type: node.type,
            position: { ...node.position },
            agent_id: node.agent_id,
            agent_display_name: node.agent_display_name,
            agent_description: node.agent_description,
            task_instructions: node.task_instructions,
            step_goal: node.step_goal,
            custom_instructions: node.custom_instructions,
            prompt_version: node.prompt_version,
            agent_revision_id: node.agent_revision_id,
            execution_receipt: node.execution_receipt,
            include_evidence: node.include_evidence,
            output_filename_template: node.output_filename_template,
            projection_plan: node.projection_plan,
            output_key: node.output_key,
            validation_attachments: node.validation_attachments?.map((attachment) => ({
              ...attachment,
            }) as Record<string, unknown>),
            validation_groups: node.validation_groups?.map((group) => ({
              ...group,
            }) as Record<string, unknown>),
          })),
          edges: flowState.edges.map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            role: edge.role,
            satisfies_binding_id: edge.satisfies_binding_id,
            replaces_attachment_id: edge.replaces_attachment_id,
            condition: edge.condition,
          })),
        }
      : undefined

  // Build AI Chat context (active tab, flow state, and Workshop state).
  const chatContext: ChatContext = {
    selected_agent_id: effectiveSelectedAgentId,
    selected_group_id: effectiveSelectedGroupId,
    view_mode: effectiveViewMode,
    trace_id: traceId || undefined,
    session_id: effectiveDurableSessionId || undefined,
    // Preserve visited flow context while moving into Workshop and back.
    active_tab: activeTab,
    flow_id: flowsVisited ? flowState?.flowId : undefined,
    flow_name: flowsVisited ? flowState?.flowName : undefined,
    flow_description: flowsVisited ? flowState?.flowDescription : undefined,
    flow_updated_at: flowsVisited ? flowState?.flowUpdatedAt : undefined,
    flow_is_dirty: flowsVisited ? flowState?.isDirty : undefined,
    flow_definition: flowDefinition,
    agent_workshop: activeTab === 'agent_workshop' ? (agentWorkshopContext || undefined) : undefined,
  }

  const captureChatContext = useCallback(async (): Promise<ChatContext> => {
    // Both editor calls copy their current values before fingerprint hashing
    // reaches the first await.
    const capturedFlow = flowsVisited
      ? (flowAuthoringContextRef.current?.captureAuthoringContext() ?? flowState)
      : null
    const capturedWorkshop = activeTab === 'agent_workshop'
      ? (workshopAuthoringContextRef.current?.captureAuthoringContext() ?? agentWorkshopContext)
      : null
    const capturedFlowDefinition: FlowContextDefinition | undefined = capturedFlow
      ? {
          version: capturedFlow.version,
          task_instructions_default_only: capturedFlow.task_instructions_default_only,
          entry_node_id: capturedFlow.entry_node_id,
          nodes: capturedFlow.nodes.map((node) => ({
            id: node.id,
            node_type: node.type,
            position: { ...node.position },
            agent_id: node.agent_id,
            agent_display_name: node.agent_display_name,
            agent_description: node.agent_description,
            task_instructions: node.task_instructions,
            step_goal: node.step_goal,
            custom_instructions: node.custom_instructions,
            prompt_version: node.prompt_version,
            agent_revision_id: node.agent_revision_id,
            execution_receipt: node.execution_receipt,
            include_evidence: node.include_evidence,
            output_filename_template: node.output_filename_template,
            projection_plan: node.projection_plan,
            output_key: node.output_key,
            validation_attachments: node.validation_attachments?.map((attachment) => ({ ...attachment })),
            validation_groups: node.validation_groups?.map((group) => ({ ...group })),
          })),
          edges: capturedFlow.edges.map((edge) => ({ ...edge })),
        }
      : undefined
    const captured: ChatContext = {
      selected_agent_id: effectiveSelectedAgentId,
      selected_group_id: effectiveSelectedGroupId,
      view_mode: effectiveViewMode,
      trace_id: traceId || undefined,
      session_id: effectiveDurableSessionId || undefined,
      active_tab: activeTab,
      flow_id: capturedFlow?.flowId,
      flow_name: capturedFlow?.flowName,
      flow_description: capturedFlow?.flowDescription,
      flow_updated_at: capturedFlow?.flowUpdatedAt,
      flow_is_dirty: capturedFlow?.isDirty,
      flow_definition: capturedFlowDefinition,
      agent_workshop: capturedWorkshop || undefined,
    }
    const fingerprinted = await fingerprintAuthoringContext(captured)
    logger.info('Captured Agent Studio authoring context', {
      component: 'AgentStudioPage',
      action: 'capture_authoring_context',
      metadata: {
        activeTab,
        hasFlowDraft: Boolean(fingerprinted.flow_definition),
        flowDirty: fingerprinted.flow_is_dirty,
        flowNodeCount: fingerprinted.flow_definition?.nodes.length ?? 0,
        hasWorkshopDraft: Boolean(fingerprinted.agent_workshop),
        workshopDirty: fingerprinted.agent_workshop?.draft_is_dirty,
        workshopToolCount: fingerprinted.agent_workshop?.draft_tool_ids?.length ?? 0,
      },
    })
    return fingerprinted
  }, [
    activeTab,
    agentWorkshopContext,
    effectiveDurableSessionId,
    effectiveSelectedAgentId,
    effectiveSelectedGroupId,
    effectiveViewMode,
    flowState,
    flowsVisited,
    traceId,
  ])

  useEffect(() => {
    const previousTab = previousAuthoringTabRef.current
    previousAuthoringTabRef.current = activeTab
    if (activeTab !== 'agent_workshop' || previousTab === 'agent_workshop') return
    setWorkshopSavedHandoff(undefined)
    setWorkshopContinuationOrigin(undefined)
    if (previousTab === 'flows') {
      void captureChatContext().then((captured) => {
        if (previousAuthoringTabRef.current === 'agent_workshop' && captured.flow_draft_fingerprint) {
          setWorkshopContinuationOrigin({
            flow_id: captured.flow_id, flow_draft_fingerprint: captured.flow_draft_fingerprint,
          })
        }
      }).catch(() => {
        setWorkshopSavedHandoff({ status: 'stale_origin' })
      })
    }
  }, [activeTab, captureChatContext])

  const handleWorkshopSavedHandoff = useCallback((handoff: WorkshopSavedHandoff) => {
    void captureChatContext().then((captured) => {
      const stale = handoff.origin
        && handoff.origin.flow_draft_fingerprint !== captured.flow_draft_fingerprint
      setWorkshopSavedHandoff(stale ? { ...handoff, status: 'stale_origin' } : handoff)
    }).catch(() => setWorkshopSavedHandoff({ ...handoff, status: 'stale_origin' }))
  }, [captureChatContext])

  const continuationLiveRef = useRef({ activeTab, workshopSavedHandoff, isClaudeStreaming })
  continuationLiveRef.current = { activeTab, workshopSavedHandoff, isClaudeStreaming }

  const handleContinueInFlow = async () => {
    const handoff = workshopSavedHandoff
    if (continuationBusyRef.current || isClaudeStreaming || handoff?.status !== 'ready'
      || !handoff.origin || !handoff.saved_agent_id || !handoff.saved_custom_agent_id) return
    const captureDrafts = () => ({
      flow: flowAuthoringContextRef.current?.captureAuthoringContext(),
      workshop: workshopAuthoringContextRef.current?.captureAuthoringContext(),
    })
    const before = captureDrafts()
    if (!before.flow || !before.workshop || before.workshop.draft_is_dirty
      || before.workshop.custom_agent_id !== handoff.saved_agent_id) {
      setWorkshopSavedHandoff({ ...handoff, status: 'stale_origin' })
      return
    }
    const beforeKey = canonicalAuthoringJson(before)
    continuationBusyRef.current = true
    setContinuingToFlow(true)
    try {
      const context = await captureChatContext()
      if (!continuationMountedRef.current || continuationLiveRef.current.activeTab !== 'agent_workshop'
        || continuationLiveRef.current.workshopSavedHandoff !== handoff) return
      if (context.flow_draft_fingerprint !== handoff.origin.flow_draft_fingerprint) {
        setWorkshopSavedHandoff({ ...handoff, status: 'stale_origin' })
        return
      }
      const reference = await getWorkshopSavedReference(handoff.saved_custom_agent_id)
      const live = continuationLiveRef.current
      if (!continuationMountedRef.current || live.activeTab !== 'agent_workshop'
        || live.workshopSavedHandoff !== handoff) return
      if (live.isClaudeStreaming || canonicalAuthoringJson(captureDrafts()) !== beforeKey) {
        setWorkshopSavedHandoff({ ...handoff, status: 'stale_origin' })
        return
      }
      if (reference.agent_id !== handoff.saved_agent_id) {
        setWorkshopSavedHandoff({ ...handoff, status: 'catalog_unavailable' })
        return
      }
      setActiveTab('flows')
      safeSetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, 'flows', {
        owner: 'preferences', key: AGENT_STUDIO_TAB_KEY,
      })
      showClaude()
      setDiscussMessage(`Propose adding the saved agent ${reference.agent_id} to my current Flow draft. Recheck its current authenticated catalog entry, preserve unrelated steps and settings, and use propose_flow_draft_update to show me the exact change for review. Do not save or execute the flow. Ask only if the insertion point is materially ambiguous.`)
      setWorkshopSavedHandoff(undefined)
    } catch {
      if (continuationMountedRef.current && continuationLiveRef.current.workshopSavedHandoff === handoff) {
        setWorkshopSavedHandoff({ ...handoff, status: 'catalog_unavailable' })
      }
    } finally {
      continuationBusyRef.current = false
      if (continuationMountedRef.current) setContinuingToFlow(false)
    }
  }

  const selectedAgentForChat =
    catalog && effectiveSelectedAgentId
      ? catalog.categories
          .flatMap((c) => c.agents)
          .find((a) => a.agent_id === effectiveSelectedAgentId)
      : undefined

  // Handle agent selection from browser
  const handleAgentSelect = (agentId: string) => {
    setSelectedAgentId(agentId)
    // Reset group selection when changing agents
    setSelectedGroupId(null)
  }

  // Handle group selection
  const handleGroupSelect = (groupId: string | null) => {
    setSelectedGroupId(groupId)
  }

  // Handle flow state changes from FlowBuilder
  const handleFlowChange = useCallback((newFlowState: FlowState) => {
    setFlowState(newFlowState)
  }, [])

  // Handle a request for AI Chat to validate the flow.
  // Include timestamp to ensure each click triggers a new request
  const handleVerifyRequest = useCallback(() => {
    showClaude()
    const message = buildFlowVerificationPrompt(
      `this curation flow "${flowState?.flowName || 'Untitled'}"`,
      Date.now(),
    )

    setVerifyMessage(message)
  }, [flowState?.flowName, showClaude])

  // Clear verify message after it's been sent
  const handleVerifyMessageSent = useCallback(() => {
    setVerifyMessage(null)
  }, [])

  // Handle discuss request from AgentDetailsPanel
  const handleDiscussWithClaude = useCallback((agentId: string, agentName: string, prompt?: string) => {
    showClaude()
    const message = prompt ?? `I'd like to discuss the **${agentName}** agent. Help me understand:
1. What this agent does and when it's used
2. Its capabilities and limitations
3. How its prompts are structured
4. Its current attached tool schemas and any PDF evidence workflow expectations

Please inspect get_prompt, get_tool_inventory, and get_tool_details before giving authoritative prompt/tool guidance.

Agent ID: ${agentId}`

    setDiscussMessage(message)
  }, [showClaude])

  const handleCloneToWorkshop = useCallback(async (agentId: string) => {
    try {
      if (agentId.startsWith('ca_')) {
        const cloned = await cloneAgentToWorkshop(agentId)
        setAgentWorkshopTemplateSource(cloned.template_source || null)
        setAgentWorkshopCustomAgentId(cloned.id)
      } else {
        setAgentWorkshopTemplateSource(agentId)
        setAgentWorkshopCustomAgentId(null)
      }
      setActiveTab('agent_workshop')
      safeSetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, 'agent_workshop', {
        owner: 'preferences',
        key: AGENT_STUDIO_TAB_KEY,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clone agent')
    }
  }, [])

  const handleWorkshopVerifyRequest = useCallback((message: string) => {
    showClaude()
    setVerifyMessage(message)
  }, [showClaude])

  // Open the Agent Browser on one agent and tab. Callers guard their own
  // unsaved edits before they call this.
  const openAgentBrowser = useCallback((request: AgentBrowserRequest) => {
    agentDetailsRequestCounterRef.current += 1
    setSelectedAgentId(request.agentId)
    setSelectedGroupId(null)
    setAgentDetailsRequest({ ...request, token: agentDetailsRequestCounterRef.current })
    applyTab('agents')
  }, [applyTab])

  const handleWorkshopViewEnvelope = useCallback((agentId: string) => {
    void confirmLeaveWorkshop().then((leave) => {
      if (!leave) return
      openAgentBrowser({ agentId, tab: 'envelope' })
    })
  }, [confirmLeaveWorkshop, openAgentBrowser])


  const handleApplyWorkshopProposal = useCallback(async (proposal: WorkshopAuthoringProposal) => {
    const workshop = workshopAuthoringContextRef.current
    if (!workshop || activeTab !== 'agent_workshop') {
      return { applied: false, message: 'Open the Workshop before applying this proposal.' }
    }
    return workshop.applyAuthoringProposal(proposal)
  }, [activeTab])

  const handleApplyFlowProposal = useCallback(async (proposal: FlowAuthoringProposal) => {
    const builder = flowAuthoringContextRef.current
    if (!builder) {
      return {
        applied: false,
        reason: 'unavailable' as const,
        message: 'Open the Flow Builder before applying this proposal.',
      }
    }
    const result = await builder.applyAuthoringProposal(proposal)
    if (result.applied) {
      setActiveTab('flows')
      safeSetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, 'flows', {
        owner: 'preferences',
        key: AGENT_STUDIO_TAB_KEY,
      })
    }
    return result
  }, [])

  // Clear discuss message after it's been sent
  const handleDiscussMessageSent = useCallback(() => {
    setDiscussMessage(null)
  }, [])

  const handleDurableSessionIdChange = useCallback((newSessionId: string) => {
    const currentSessionId = normalizeSearchParam(searchParamsRef.current.get('session_id'))
    if (currentSessionId === newSessionId) {
      return
    }

    setPendingUrlSwapSessionId(newSessionId)
    const nextSearchParams = new URLSearchParams(searchParamsRef.current)
    nextSearchParams.set('session_id', newSessionId)
    searchParamsRef.current = nextSearchParams
    setSearchParams(nextSearchParams, { replace: true })
  }, [setSearchParams])

  const chatElement = (variant: 'panel' | 'drawer', panelId: string) => (
    <OpusChat
      context={chatContext}
      captureContext={captureChatContext}
      initialConversation={seededConversation}
      durableSessionId={effectiveDurableSessionId}
      sourceSessionId={transcriptSourceSessionId}
      selectedAgent={selectedAgentForChat}
      verifyMessage={verifyMessage}
      onVerifyMessageSent={handleVerifyMessageSent}
      discussMessage={discussMessage}
      onDiscussMessageSent={handleDiscussMessageSent}
      onDurableSessionIdChange={handleDurableSessionIdChange}
      onConversationSnapshotChange={handleConversationSnapshotChange}
      onApplyFlowProposal={handleApplyFlowProposal}
      onApplyWorkshopProposal={handleApplyWorkshopProposal}
      variant={variant}
      panelId={panelId}
      onHide={hideClaude}
      inputRef={chatInputRef}
      onStreamingChange={setIsClaudeStreaming}
    />
  )

  if (error || durableTranscriptError) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{error ?? durableTranscriptError?.message}</Alert>
      </Box>
    )
  }

  return (
    <Root>
      {/* Loading overlay with blur effect */}
      <Backdrop
        sx={{
          color: '#fff',
          zIndex: (theme) => theme.zIndex.drawer + 1,
          backdropFilter: 'blur(4px)',
        }}
        open={loading || durableTranscriptLoading}
      >
        <Stack spacing={2} alignItems="center">
          <CircularProgress color="inherit" size={60} />
          <Typography variant="h6" color="inherit">
            {durableTranscriptLoading ? 'Hydrating durable chat...' : 'Initializing...'}
          </Typography>
        </Stack>
      </Backdrop>

      <PanelGroup
        direction="horizontal"
        autoSaveId={AGENT_STUDIO_PANELS_AUTOSAVE_ID}
        style={{ flex: 1, minWidth: 0, height: '100%', display: 'flex', overflow: 'hidden' }}
      >
        {/* Work surface: tabbed interface */}
        <Panel id="work" order={1} defaultSize={70} minSize={50}>
          <PanelCard>
            <TabBar>
              <StyledTabs
                value={activeTab}
                onChange={handleTabChange}
                aria-label="Agent Studio tabs"
              >
                <StyledTab
                  value="agents"
                  label="Agents"
                  icon={<DescriptionIcon sx={{ fontSize: 18 }} />}
                  iconPosition="start"
                />
                <StyledTab
                  value="flows"
                  label="Flows"
                  icon={<AccountTreeIcon sx={{ fontSize: 18 }} />}
                  iconPosition="start"
                />
                <StyledTab
                  value="agent_workshop"
                  label="Agent Workshop"
                  icon={<ScienceIcon sx={{ fontSize: 18 }} />}
                  iconPosition="start"
                />
              </StyledTabs>
              {isNarrow && (
                <>
                  <Badge
                    variant="dot"
                    color="warning"
                    invisible={unreadCount === 0}
                    sx={{
                      '& .MuiBadge-badge': {
                        width: 8,
                        height: 8,
                        minWidth: 8,
                        border: '2px solid',
                        borderColor: 'background.paper',
                      },
                    }}
                  >
                    <Button
                      ref={launcherButtonRef}
                      variant="outlined"
                      size="small"
                      startIcon={<AutoAwesomeIcon sx={{ fontSize: 16 }} />}
                      aria-expanded={drawerOpen}
                      aria-controls={CLAUDE_DRAWER_ID}
                      aria-describedby={unreadCount > 0 ? LAUNCHER_UNREAD_DESCRIPTION_ID : undefined}
                      onClick={toggleClaude}
                      sx={{
                        height: 28,
                        textTransform: 'none',
                        fontSize: '0.8rem',
                        '&:focus-visible': {
                          outline: '2px solid',
                          outlineColor: 'primary.main',
                          outlineOffset: 1,
                        },
                      }}
                    >
                      AI Chat
                    </Button>
                  </Badge>
                  {unreadCount > 0 && (
                    <VisuallyHidden id={LAUNCHER_UNREAD_DESCRIPTION_ID}>
                      {formatUnreadDescription(unreadCount)}
                    </VisuallyHidden>
                  )}
                </>
              )}
            </TabBar>

            <TabContent>
              {activeTab === 'agents' && catalog && (
                <AgentBrowser
                  catalog={catalog}
                  selectedAgentId={selectedAgentId}
                  selectedGroupId={selectedGroupId}
                  onAgentSelect={handleAgentSelect}
                  onGroupSelect={handleGroupSelect}
                  onDiscussWithClaude={handleDiscussWithClaude}
                  onCloneToWorkshop={handleCloneToWorkshop}
                  detailsRequest={agentDetailsRequest}
                />
              )}
              {flowsVisited && (
                <Box
                  data-testid="flows-tab-panel"
                  hidden={activeTab !== 'flows'}
                  sx={{ height: '100%', minHeight: 0, '&[hidden]': { display: 'none' } }}
                >
                  <FlowBuilder
                    flowId={currentFlowId}
                    onFlowSaved={(flowId) => setCurrentFlowId(flowId)}
                    onFlowChange={handleFlowChange}
                    onVerifyRequest={handleVerifyRequest}
                    onOpenAgent={openAgentBrowser}
                    active={activeTab === 'flows'}
                    authoringContextRef={flowAuthoringContextRef}
                  />
                </Box>
              )}
              {activeTab === 'agent_workshop' && catalog && (
                <>
                {workshopSavedHandoff && (
                  <Alert severity={workshopSavedHandoff.status === 'ready' ? 'success' : 'warning'} role="status"
                    action={workshopSavedHandoff.status === 'ready' && workshopSavedHandoff.origin ? (
                      <Button color="inherit" disabled={continuingToFlow || isClaudeStreaming}
                        onClick={() => void handleContinueInFlow()}>
                        {continuingToFlow ? 'Checking…' : 'Review in Flow'}
                      </Button>
                    ) : undefined}>
                    {workshopSavedHandoff.status === 'ready'
                      ? `Saved agent ${workshopSavedHandoff.saved_agent_id} is available in the refreshed catalog.`
                      : 'The agent handoff needs a fresh catalog or flow review before continuation.'}
                  </Alert>
                )}
                <PromptWorkshop
                  catalog={catalog}
                  continuationOrigin={workshopContinuationOrigin}
                  onSavedHandoff={handleWorkshopSavedHandoff}
                  initialParentAgentId={agentWorkshopTemplateSource}
                  initialCustomAgentId={agentWorkshopCustomAgentId}
                  onContextChange={setAgentWorkshopContext}
                  onVerifyRequest={handleWorkshopVerifyRequest}
                  opusConversation={opusConversation}
                  onViewEnvelope={handleWorkshopViewEnvelope}
                  leaveGuardRef={workshopLeaveGuardRef}
                  authoringContextRef={workshopAuthoringContextRef}
                />
                </>
              )}
            </TabContent>
          </PanelCard>
        </Panel>

        {!isNarrow && (
          <>
            <ResizeHandle collapsed={claudeCollapsed} />

            {/* Provider-neutral AI Chat pane */}
            <Panel
              id="claude"
              order={2}
              ref={claudePanelRef}
              defaultSize={30}
              minSize={22}
              maxSize={50}
              collapsible
              collapsedSize={0}
              onCollapse={handlePanelCollapse}
              onExpand={handlePanelExpand}
            >
              <ClaudePanelSection
                id={CLAUDE_PANEL_ID}
                collapsed={claudeCollapsed}
                aria-hidden={claudeCollapsed ? 'true' : undefined}
              >
                {chatElement('panel', CLAUDE_PANEL_ID)}
              </ClaudePanelSection>
            </Panel>
          </>
        )}
      </PanelGroup>

      {!isNarrow && claudeCollapsed && (
        <ClaudeRail
          ref={railButtonRef}
          panelId={CLAUDE_PANEL_ID}
          unreadCount={unreadCount}
          isStreaming={isClaudeStreaming}
          onShow={showClaude}
        />
      )}

      {isNarrow && (
        <ClaudeDrawer
          id={CLAUDE_DRAWER_ID}
          open={drawerOpen}
          fullWidth={isFullWidthDrawer}
          onClose={hideClaude}
        >
          {chatElement('drawer', CLAUDE_DRAWER_ID)}
        </ClaudeDrawer>
      )}
    </Root>
  )
}

export default AgentStudioPage
