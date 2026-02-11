/**
 * Agent Studio Page
 *
 * Two-panel layout for exploring agent prompts, building flows, and chatting with Opus 4.5:
 * - Left panel: Chat with Claude Opus 4.5 for guidance
 * - Right panel: Tabbed interface with [Prompts] and [Flows] tabs
 *
 * Entry points:
 * 1. Nav bar link to /agent-studio (fresh start)
 * 2. Triple-dot menu "Open in Agent Studio" with trace context
 */

import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Box, Backdrop, CircularProgress, Alert, Typography, Stack, Tabs, Tab } from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import DescriptionIcon from '@mui/icons-material/Description'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import ScienceIcon from '@mui/icons-material/Science'

import OpusChat from '@/components/AgentStudio/OpusChat'
import AgentBrowser from '@/components/AgentStudio/AgentBrowser'
import { FlowBuilder, type FlowState } from '@/components/AgentStudio/FlowBuilder'
import PromptWorkshop from '@/components/AgentStudio/PromptWorkshop/PromptWorkshop'
import { fetchPromptCatalog } from '@/services/agentStudioService'
import type { PromptCatalog, ChatContext, PromptWorkshopContext } from '@/types/promptExplorer'

const Root = styled(Box)(({ theme }) => ({
  flex: 1,
  display: 'flex',
  height: '100%',
  overflow: 'hidden',
  padding: theme.spacing(2),
  paddingTop: theme.spacing(1.5),
}))

const PanelSection = styled(Box)(() => ({
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  height: '100%',
  paddingTop: 0,
  '& > *': {
    flex: 1,
    minHeight: 0,
    height: '100%',
  },
}))

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

const RightPanelContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
}))

const StyledTabs = styled(Tabs)(({ theme }) => ({
  minHeight: 40,
  borderBottom: `1px solid ${theme.palette.divider}`,
  '& .MuiTabs-indicator': {
    height: 3,
  },
}))

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

type TabValue = 'agents' | 'flows' | 'prompt_workshop'

// localStorage key for tab persistence
const AGENT_STUDIO_TAB_KEY = 'agent-studio-tab'

function AgentStudioPage() {
  const [searchParams] = useSearchParams()

  // Data state
  const [catalog, setCatalog] = useState<PromptCatalog | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // UI state (with persistence)
  const [activeTab, setActiveTab] = useState<TabValue>(() => {
    const stored = localStorage.getItem(AGENT_STUDIO_TAB_KEY)
    // Migrate old 'prompts' value to 'agents' (tab was renamed)
    if (stored === 'prompts') {
      localStorage.setItem(AGENT_STUDIO_TAB_KEY, 'agents')
      return 'agents'
    }
    return (stored === 'agents' || stored === 'flows' || stored === 'prompt_workshop') ? stored : 'agents'
  })

  // Persist tab changes
  const handleTabChange = useCallback((_e: React.SyntheticEvent, newValue: TabValue) => {
    setActiveTab(newValue)
    localStorage.setItem(AGENT_STUDIO_TAB_KEY, newValue)
  }, [])
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [selectedModId, setSelectedModId] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'base' | 'mod' | 'combined'>('base')
  const [currentFlowId, setCurrentFlowId] = useState<string | null>(null)
  const [promptWorkshopParentAgentId, setPromptWorkshopParentAgentId] = useState<string | null>(null)
  const [promptWorkshopContext, setPromptWorkshopContext] = useState<PromptWorkshopContext | null>(null)
  const [flowState, setFlowState] = useState<FlowState | null>(null)
  const [verifyMessage, setVerifyMessage] = useState<string | null>(null)
  const [discussMessage, setDiscussMessage] = useState<string | null>(null)

  // Get trace_id from URL params (when coming from triple-dot menu)
  const traceId = searchParams.get('trace_id')

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

  const workshopSelectedAgentId = promptWorkshopContext?.custom_agent_id || promptWorkshopContext?.parent_agent_id
  const workshopSelectedModId = promptWorkshopContext?.selected_mod_id

  const effectiveSelectedAgentId =
    activeTab === 'prompt_workshop' ? workshopSelectedAgentId : (selectedAgentId || undefined)
  const effectiveSelectedModId =
    activeTab === 'prompt_workshop' ? workshopSelectedModId : (selectedModId || undefined)
  const effectiveViewMode =
    activeTab === 'prompt_workshop'
      ? (effectiveSelectedModId ? 'combined' : 'base')
      : viewMode

  // Build chat context for Opus (includes active tab, flow state, and prompt workshop state)
  const chatContext: ChatContext = {
    selected_agent_id: effectiveSelectedAgentId,
    selected_mod_id: effectiveSelectedModId,
    view_mode: effectiveViewMode,
    trace_id: traceId || undefined,
    // Flow context (when on flows tab)
    active_tab: activeTab,
    flow_name: activeTab === 'flows' ? flowState?.flowName : undefined,
    flow_definition: activeTab === 'flows' && flowState ? {
      nodes: flowState.nodes,
      edges: flowState.edges,
    } : undefined,
    prompt_workshop: activeTab === 'prompt_workshop' ? (promptWorkshopContext || undefined) : undefined,
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
    // Reset MOD selection when changing agents
    setSelectedModId(null)
    setViewMode('base')
  }

  // Handle MOD selection
  const handleModSelect = (modId: string | null) => {
    setSelectedModId(modId)
    setViewMode(modId ? 'combined' : 'base')
  }

  // Handle flow state changes from FlowBuilder
  const handleFlowChange = useCallback((newFlowState: FlowState) => {
    setFlowState(newFlowState)
  }, [])

  // Handle verify request - sends a message to Claude to validate the flow
  // Include timestamp to ensure each click triggers a new request
  const handleVerifyRequest = useCallback(() => {
    // Build a verification message for Claude
    const message = `Verify this curation flow "${flowState?.flowName || 'Untitled'}".

REQUIRED: Call these tools first:
1. get_current_flow() - get flow definition with task_instructions and custom_instructions
2. get_available_agents() - get agent categories and output_agents list

CRITICAL ERRORS (must fail verification):
- task_input node has EMPTY task_instructions (this is required content)
- Disconnected nodes (won't execute)
- Cycles (infinite loops)
- Input sources referencing non-existent outputs

HIGH PRIORITY ISSUES:
- Flow doesn't end with an output-category agent
- Duplicate output_key values

SUGGESTIONS (only if evidence-based):
- ONLY suggest alternative agents if the curator's task_instructions or custom_instructions explicitly mention something that a different agent handles better
- Example: If instructions say "extract gene expression patterns" but gene_expression agent is missing, suggest it
- Do NOT make speculative suggestions without evidence from the instructions
- If instructions are empty, there is nothing to suggest

OUTPUT:
### FLOW VERIFICATION: [PASS/FAIL]
**Critical:** [list or "None"]
**High:** [list or "None"]
**Suggestions:** [evidence-based only, or "None"]

[Request ID: ${Date.now()}]`

    setVerifyMessage(message)
  }, [flowState?.flowName])

  // Clear verify message after it's been sent
  const handleVerifyMessageSent = useCallback(() => {
    setVerifyMessage(null)
  }, [])

  // Handle discuss request from AgentDetailsPanel
  const handleDiscussWithClaude = useCallback((agentId: string, agentName: string) => {
    const message = `I'd like to discuss the **${agentName}** agent. Help me understand:
1. What this agent does and when it's used
2. Its capabilities and limitations
3. How its prompts are structured

Agent ID: ${agentId}`

    setDiscussMessage(message)
  }, [])

  const handleCloneToWorkshop = useCallback((agentId: string) => {
    setPromptWorkshopParentAgentId(agentId)
    setActiveTab('prompt_workshop')
    localStorage.setItem(AGENT_STUDIO_TAB_KEY, 'prompt_workshop')
  }, [])

  const handleWorkshopVerifyRequest = useCallback((message: string) => {
    setVerifyMessage(message)
  }, [])

  // Clear discuss message after it's been sent
  const handleDiscussMessageSent = useCallback(() => {
    setDiscussMessage(null)
  }, [])

  if (error) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{error}</Alert>
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
        open={loading}
      >
        <Stack spacing={2} alignItems="center">
          <CircularProgress color="inherit" size={60} />
          <Typography variant="h6" color="inherit">
            Initializing...
          </Typography>
        </Stack>
      </Backdrop>

      <PanelGroup
        direction="horizontal"
        autoSaveId="agent-studio-panels"
        style={{ width: '100%', height: '100%', display: 'flex', overflow: 'hidden' }}
      >
        {/* Left Panel: Opus Chat */}
        <Panel defaultSize={40} minSize={25} maxSize={60}>
          <PanelSection sx={{ pr: 1 }}>
            <OpusChat
              context={chatContext}
              selectedAgent={selectedAgentForChat}
              verifyMessage={verifyMessage}
              onVerifyMessageSent={handleVerifyMessageSent}
              discussMessage={discussMessage}
              onDiscussMessageSent={handleDiscussMessageSent}
            />
          </PanelSection>
        </Panel>

        <ResizeHandle />

        {/* Right Panel: Tabbed Interface */}
        <Panel defaultSize={60} minSize={40} maxSize={75}>
          <PanelSection sx={{ pl: 1 }}>
            <RightPanelContainer>
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
                  value="prompt_workshop"
                  label="Prompt Workshop"
                  icon={<ScienceIcon sx={{ fontSize: 18 }} />}
                  iconPosition="start"
                />
              </StyledTabs>

              <TabContent>
                {activeTab === 'agents' && catalog && (
                  <AgentBrowser
                    catalog={catalog}
                    selectedAgentId={selectedAgentId}
                    selectedModId={selectedModId}
                    viewMode={viewMode}
                    onAgentSelect={handleAgentSelect}
                    onModSelect={handleModSelect}
                    onViewModeChange={setViewMode}
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
                {activeTab === 'prompt_workshop' && catalog && (
                  <PromptWorkshop
                    catalog={catalog}
                    initialParentAgentId={promptWorkshopParentAgentId}
                    onContextChange={setPromptWorkshopContext}
                    onVerifyRequest={handleWorkshopVerifyRequest}
                  />
                )}
              </TabContent>
            </RightPanelContainer>
          </PanelSection>
        </Panel>
      </PanelGroup>
    </Root>
  )
}

export default AgentStudioPage
