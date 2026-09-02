/**
 * Agent Studio Page
 *
 * Adaptive shell for exploring agent prompts, building flows, and chatting with Claude:
 * - Left: work surface with the Agents, Flows, and Agent Workshop tabs
 * - Right: Claude copilot pane (30% by default) that collapses to a 44px rail
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
import { FlowBuilder, type FlowState } from '@/components/AgentStudio/FlowBuilder'
import PromptWorkshop from '@/components/AgentStudio/PromptWorkshop/PromptWorkshop'
import {
  useChatHistoryDetailQuery,
  useChatHistoryTranscriptQuery,
} from '@/features/history/useChatHistoryQuery'
import { cloneAgentToWorkshop, fetchPromptCatalog } from '@/services/agentStudioService'
import {
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  buildRestorableChatMessages,
} from '@/services/chatHistoryApi'
import { safeGetItem, safeRemoveItem, safeSetItem } from '@/lib/browserStorage'
import type {
  PromptCatalog,
  ChatContext,
  FlowContextDefinition,
  AgentWorkshopContext,
  ToolIdeaConversationEntry,
  WorkshopPromptUpdateProposal,
  WorkshopPromptUpdateRequest,
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

  // Persist tab changes
  const handleTabChange = useCallback((_e: React.SyntheticEvent, newValue: TabValue) => {
    setActiveTab(newValue)
    safeSetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, newValue, {
      owner: 'preferences',
      key: AGENT_STUDIO_TAB_KEY,
    })
  }, [])
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null)
  const [currentFlowId, setCurrentFlowId] = useState<string | null>(null)
  const [agentWorkshopTemplateSource, setAgentWorkshopTemplateSource] = useState<string | null>(null)
  const [agentWorkshopCustomAgentId, setAgentWorkshopCustomAgentId] = useState<string | null>(null)
  const [agentWorkshopContext, setAgentWorkshopContext] = useState<AgentWorkshopContext | null>(null)
  const [flowState, setFlowState] = useState<FlowState | null>(null)
  const [verifyMessage, setVerifyMessage] = useState<string | null>(null)
  const [discussMessage, setDiscussMessage] = useState<string | null>(null)
  const [opusConversation, setOpusConversation] = useState<ToolIdeaConversationEntry[]>([])
  const [workshopPromptUpdateRequest, setWorkshopPromptUpdateRequest] = useState<WorkshopPromptUpdateRequest | null>(null)
  const [pendingUrlSwapSessionId, setPendingUrlSwapSessionId] = useState<string | null>(null)
  const promptUpdateCounterRef = useRef(0)
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

  // Ctrl+. / Cmd+. toggles Claude from anywhere on the page.
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

  // Unread tracking: assistant messages appended while Claude is hidden. The
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
  // Note: trace context is NOT fetched here - it's injected into Opus's prompt on the backend
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
    activeTab === 'flows' && flowState
      ? {
          version: flowState.version,
          entry_node_id: flowState.entry_node_id,
          nodes: flowState.nodes.map((node) => ({
            id: node.id,
            node_type: node.type,
            agent_id: node.agent_id,
            agent_display_name: node.agent_display_name,
            task_instructions: node.task_instructions,
            step_goal: node.step_goal,
            custom_instructions: node.custom_instructions,
            prompt_version: node.prompt_version,
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
          })),
        }
      : undefined

  // Build chat context for Opus (includes active tab, flow state, and agent workshop state)
  const chatContext: ChatContext = {
    selected_agent_id: effectiveSelectedAgentId,
    selected_group_id: effectiveSelectedGroupId,
    view_mode: effectiveViewMode,
    trace_id: traceId || undefined,
    session_id: effectiveDurableSessionId || undefined,
    // Flow context (when on flows tab)
    active_tab: activeTab,
    flow_name: activeTab === 'flows' ? flowState?.flowName : undefined,
    flow_definition: flowDefinition,
    agent_workshop: activeTab === 'agent_workshop' ? (agentWorkshopContext || undefined) : undefined,
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

  // Handle verify request - sends a message to Claude to validate the flow
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
  const handleDiscussWithClaude = useCallback((agentId: string, agentName: string) => {
    showClaude()
    const message = `I'd like to discuss the **${agentName}** agent. Help me understand:
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

  const handleApplyWorkshopPromptUpdate = useCallback((proposal: WorkshopPromptUpdateProposal) => {
    promptUpdateCounterRef.current += 1
    setActiveTab('agent_workshop')
    safeSetItem(() => window.localStorage, AGENT_STUDIO_TAB_KEY, 'agent_workshop', {
      owner: 'preferences',
      key: AGENT_STUDIO_TAB_KEY,
    })
    setWorkshopPromptUpdateRequest({
      request_id: promptUpdateCounterRef.current,
      prompt: proposal.prompt,
      summary: proposal.summary,
      apply_mode: proposal.apply_mode || 'replace',
      target_prompt: proposal.target_prompt || 'main',
      target_group_id: proposal.target_group_id,
    })
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
      onApplyWorkshopPromptUpdate={handleApplyWorkshopPromptUpdate}
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
                      Claude
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
                />
              )}
              {activeTab === 'flows' && (
                <FlowBuilder
                  flowId={currentFlowId}
                  onFlowSaved={(flowId) => setCurrentFlowId(flowId)}
                  onFlowChange={handleFlowChange}
                  onVerifyRequest={handleVerifyRequest}
                />
              )}
              {activeTab === 'agent_workshop' && catalog && (
                <PromptWorkshop
                  catalog={catalog}
                  initialParentAgentId={agentWorkshopTemplateSource}
                  initialCustomAgentId={agentWorkshopCustomAgentId}
                  onContextChange={setAgentWorkshopContext}
                  onVerifyRequest={handleWorkshopVerifyRequest}
                  opusConversation={opusConversation}
                  incomingPromptUpdate={workshopPromptUpdateRequest}
                />
              )}
            </TabContent>
          </PanelCard>
        </Panel>

        {!isNarrow && (
          <>
            <ResizeHandle collapsed={claudeCollapsed} />

            {/* Claude copilot pane */}
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
