/**
 * AgentDetailsPanel Component
 *
 * Detail view for the agent selected in Agent Browser: a compact header
 * (name, summary, Discuss with Claude, Clone to Workshop) and three tabs:
 * - Guide: when to use it, what it reads, capabilities, limitations, tools
 * - Envelope: the domain pack's objects, fields, and automatic checks
 *   (only when the agent declares a domain pack)
 * - Prompts: one prompt layer at a time with an effective view
 */

import { useCallback, useEffect, useState } from 'react'
import { Alert, Box, Button, Tab, Tabs, Typography } from '@mui/material'
import { styled } from '@mui/material/styles'
import ChatIcon from '@mui/icons-material/Chat'
import ScienceIcon from '@mui/icons-material/Science'
import AutoAwesomeOutlinedIcon from '@mui/icons-material/AutoAwesomeOutlined'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'

import { fetchAllTools, fetchCombinedPrompt } from '@/services/agentStudioService'
import type { CombinedPromptResponse, PromptInfo } from '@/types/promptExplorer'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import ToolDetailsDialog from './ToolDetailsDialog'
import AgentGuideTab from './AgentGuideTab'
import type { AgentBrowserFocus, AgentDetailsRequest } from './agentBrowserRequest'
import EnvelopeTab from './EnvelopeTab'
import AgentPromptsTab from './AgentPromptsTab'

const PanelContainer = styled(Box)(() => ({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  minWidth: 0,
  overflow: 'hidden',
}))

const StyledTabs = styled(Tabs)(() => ({
  minHeight: 36,
  '& .MuiTabs-indicator': { height: 2 },
}))

const StyledTab = styled(Tab)(({ theme }) => ({
  minHeight: 36,
  minWidth: 0,
  textTransform: 'none',
  fontWeight: 500,
  fontSize: 13,
  padding: theme.spacing(0.75, 1.25, 1),
}))

const TabContent = styled(Box)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  overflow: 'auto',
  padding: theme.spacing(2, 2.5, 3),
}))

const EmptyState = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: '100%',
  color: theme.palette.text.secondary,
  padding: theme.spacing(4),
  textAlign: 'center',
}))

type TabValue = 'guide' | 'envelope' | 'prompts'

interface AgentDetailsPanelProps {
  agent: PromptInfo | null
  selectedGroupId: string | null
  onGroupSelect: (groupId: string | null) => void
  /**
   * Discuss-with-Claude handoff. The optional third argument carries a
   * drafting prompt when the curator asks Claude to draft a guide.
   */
  onDiscussWithClaude?: (agentId: string, agentName: string, prompt?: string) => void
  onCloneToWorkshop?: (agentId: string) => void
  /** Rendered as a "Back to Agents" control at narrow widths. */
  onBack?: () => void
  /** True when the browser is below the narrow-width threshold. */
  narrow?: boolean
  /** Deep link: switch to a tab, and focus one envelope field, when a new request arrives. */
  request?: AgentDetailsRequest | null
}

function draftGuidePrompt(agentId: string, agentName: string): string {
  return `Draft a curator guide for the **${agentName}** agent. Write, in curator voice:
1. A one-sentence summary of what it does
2. When to use it and when not to use it
3. Its capabilities, each with one example query and result
4. Its limitations

Inspect get_prompt, get_tool_inventory, and get_tool_details first and base every statement on what you find. Do not invent behavior the prompts and tools do not show.

Agent ID: ${agentId}`
}

function AgentDetailsPanel({
  agent,
  selectedGroupId,
  onGroupSelect,
  onDiscussWithClaude,
  onCloneToWorkshop,
  onBack,
  narrow = false,
  request = null,
}: AgentDetailsPanelProps) {
  const { agents: agentMetadata } = useAgentMetadata()
  const [activeTab, setActiveTab] = useState<TabValue>('guide')
  const [envelopeFocus, setEnvelopeFocus] = useState<AgentBrowserFocus | null>(null)
  const [combinedPrompt, setCombinedPrompt] = useState<CombinedPromptResponse | null>(null)
  const [loadingCombined, setLoadingCombined] = useState(false)
  const [selectedTool, setSelectedTool] = useState<string | null>(null)
  const [toolDescriptions, setToolDescriptions] = useState<Record<string, string>>({})
  const [toolInventoryError, setToolInventoryError] = useState<string | null>(null)
  const [toolInventoryAttempt, setToolInventoryAttempt] = useState(0)

  const domainEnvelopeMetadata = agent
    ? agentMetadata[agent.agent_id]?.domain_envelope
    : undefined
  const allowedGroupIds = agent
    ? agentMetadata[agent.agent_id]?.allowed_group_ids || []
    : []

  // Tool purposes for the tools table. Reloads when the curator retries.
  useEffect(() => {
    let cancelled = false
    setToolInventoryError(null)
    fetchAllTools()
      .then((tools) => {
        if (cancelled) return
        setToolDescriptions(
          Object.fromEntries(
            Object.entries(tools).map(([toolId, info]) => [toolId, info.description])
          )
        )
      })
      .catch((err) => {
        if (cancelled) return
        setToolInventoryError(err instanceof Error ? err.message : 'Failed to load tool inventory')
      })
    return () => {
      cancelled = true
    }
  }, [toolInventoryAttempt])

  const handleRetryToolInventory = useCallback(() => {
    setToolInventoryAttempt((attempt) => attempt + 1)
  }, [])

  // Load the combined prompt for the selected group.
  useEffect(() => {
    if (agent?.custom_prompt_overlay_status === 'needs_review') {
      setCombinedPrompt(null)
      setLoadingCombined(false)
      return
    }
    if (agent && selectedGroupId && agent.has_group_rules) {
      setLoadingCombined(true)
      fetchCombinedPrompt(agent.agent_id, selectedGroupId)
        .then((result) => setCombinedPrompt(result))
        .catch((err) => {
          console.error('Failed to fetch combined prompt:', err)
          setCombinedPrompt(null)
        })
        .finally(() => setLoadingCombined(false))
      return
    }
    setCombinedPrompt(null)
  }, [agent, selectedGroupId])

  useEffect(() => {
    if (activeTab === 'envelope' && !domainEnvelopeMetadata) {
      setActiveTab('guide')
    }
  }, [activeTab, domainEnvelopeMetadata])

  // Each request carries a fresh token, so the same tab can be asked for twice.
  useEffect(() => {
    if (!request || request.agentId !== agent?.agent_id) return
    setActiveTab(request.tab)
    setEnvelopeFocus(request.focus ?? null)
  }, [request, agent?.agent_id])

  const handleTabChange = (_: React.SyntheticEvent, newValue: TabValue) => {
    setActiveTab(newValue)
  }

  const handleDiscuss = () => {
    if (agent && onDiscussWithClaude) {
      onDiscussWithClaude(agent.agent_id, agent.agent_name)
    }
  }

  const handleDraftGuide = () => {
    if (agent && onDiscussWithClaude) {
      onDiscussWithClaude(agent.agent_id, agent.agent_name, draftGuidePrompt(agent.agent_id, agent.agent_name))
    }
  }

  const handleCloneToWorkshop = () => {
    if (agent && onCloneToWorkshop) {
      onCloneToWorkshop(agent.agent_id)
    }
  }

  if (!agent) {
    return (
      <EmptyState>
        <Box sx={{ maxWidth: 360 }}>
          <AutoAwesomeOutlinedIcon sx={{ fontSize: 48, mb: 2, opacity: 0.5 }} />
          <Typography variant="h6" sx={{ mb: 1 }}>
            Browse your agents
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Pick an agent on the left to see what it does, the tools it uses, and the validation that applies.
          </Typography>
        </Box>
      </EmptyState>
    )
  }

  const { documentation } = agent
  const canCloneToWorkshop = agent.agent_id !== 'task_input'
  const summary = documentation?.summary || agent.description

  return (
    <PanelContainer>
      <Box sx={{ px: 2.5, pt: 1.75, display: 'flex', flexDirection: 'column', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5, flexWrap: 'wrap' }}>
          {onBack && (
            <Button
              size="small"
              variant="outlined"
              startIcon={<ArrowBackIcon />}
              onClick={onBack}
              aria-label="Back to Agents"
              sx={{ textTransform: 'none', flex: 'none', height: 26, fontSize: 12, px: 1.125 }}
            >
              Agents
            </Button>
          )}
          <Box sx={{ flex: '1 1 320px', minWidth: 0 }}>
            <Typography
              component="h2"
              sx={{ m: 0, fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em', lineHeight: 1.3, overflowWrap: 'anywhere' }}
            >
              {agent.agent_name}
            </Typography>
            {summary && (
              <Typography sx={{ mt: 0.25, fontSize: 13.5, color: 'text.secondary', maxWidth: '66ch' }}>
                {summary}
              </Typography>
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: 1, flex: 'none', flexWrap: 'wrap' }}>
            <Button
              variant="contained"
              size="small"
              disableElevation
              startIcon={<ChatIcon />}
              onClick={handleDiscuss}
              sx={{ whiteSpace: 'nowrap', textTransform: 'none' }}
            >
              Discuss with Claude
            </Button>
            {canCloneToWorkshop && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<ScienceIcon />}
                onClick={handleCloneToWorkshop}
                sx={{ whiteSpace: 'nowrap', textTransform: 'none' }}
              >
                Clone to Workshop
              </Button>
            )}
          </Box>
        </Box>
        {allowedGroupIds.length > 0 && (
          <Alert
            severity="info"
            icon={<LockOutlinedIcon fontSize="inherit" />}
            sx={{ py: 0.25, '& .MuiAlert-message': { fontSize: 12.5 } }}
          >
            Available to groups: {allowedGroupIds.join(', ')}.
            {!agent.agent_id.startsWith('ca_')
              ? ' This package-owned system restriction is read-only.'
              : ' Sharing and group-specific instructions do not widen this access restriction.'}
          </Alert>
        )}
      </Box>

      <Box sx={{ borderBottom: 1, borderColor: 'divider', px: 2.5, pt: 0.75 }}>
        <StyledTabs value={activeTab} onChange={handleTabChange} aria-label="Agent detail sections">
          <StyledTab label="Guide" value="guide" />
          {domainEnvelopeMetadata && (
            <StyledTab label="Envelope" value="envelope" />
          )}
          <StyledTab label="Prompts" value="prompts" />
        </StyledTabs>
      </Box>

      <TabContent>
        {activeTab === 'guide' && (
          <AgentGuideTab
            documentation={documentation}
            tools={agent.tools}
            toolDescriptions={toolDescriptions}
            toolInventoryError={toolInventoryError}
            onRetryToolInventory={handleRetryToolInventory}
            narrow={narrow}
            onShowToolDetails={setSelectedTool}
            onDraftGuide={handleDraftGuide}
          />
        )}

        {activeTab === 'envelope' && domainEnvelopeMetadata && (
          <EnvelopeTab metadata={domainEnvelopeMetadata} narrow={narrow} focus={envelopeFocus} />
        )}

        {activeTab === 'prompts' && (
          <AgentPromptsTab
            agent={agent}
            selectedGroupId={selectedGroupId}
            onGroupSelect={onGroupSelect}
            combinedPrompt={combinedPrompt}
            loadingCombined={loadingCombined}
          />
        )}
      </TabContent>

      <ToolDetailsDialog
        open={selectedTool !== null}
        onClose={() => setSelectedTool(null)}
        toolId={selectedTool}
        agentId={agent.agent_id}
        agentName={agent.agent_name}
      />
    </PanelContainer>
  )
}

export default AgentDetailsPanel
