/**
 * PromptViewer Component
 *
 * Slide-over panel that displays base prompts and MOD-specific prompts
 * for an agent. Covers the flow canvas when open.
 */

import { useState, useEffect } from 'react'
import {
  Box,
  Typography,
  Paper,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Tooltip,
  Chip,
  CircularProgress,
  Alert,
  Slide,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import CloseIcon from '@mui/icons-material/Close'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import CheckIcon from '@mui/icons-material/Check'

import type { PromptInfo } from '@/types/promptExplorer'
import { fetchPromptCatalog, fetchCombinedPrompt } from '@/services/agentStudioService'
import logger from '@/services/logger'

const ViewerContainer = styled(Paper)(({ theme }) => ({
  position: 'absolute',
  top: 0,
  left: 0,
  bottom: 0,
  right: 0,
  backgroundColor: theme.palette.background.paper,
  display: 'flex',
  flexDirection: 'column',
  zIndex: 20,
  overflow: 'hidden',
}))

const ViewerHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderBottom: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(2),
  backgroundColor: alpha(theme.palette.primary.main, 0.05),
}))

const ViewerContent = styled(Box)(({ theme }) => ({
  flex: 1,
  overflow: 'auto',
  padding: theme.spacing(2),
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(2),
}))

const PromptContent = styled(Paper)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  padding: theme.spacing(2),
  backgroundColor: alpha(theme.palette.background.default, 0.5),
  fontFamily: 'monospace',
  fontSize: '0.8rem',
  lineHeight: 1.6,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  overflow: 'auto',
  border: `1px solid ${theme.palette.divider}`,
}))

const ControlsRow = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(2),
  flexWrap: 'wrap',
}))

type ViewMode = 'base' | 'mod' | 'combined'

interface PromptViewerProps {
  /** Agent ID to display prompts for */
  agentId: string
  /** Agent display name */
  agentName: string
  /** Whether the viewer is open */
  open: boolean
  /** Callback to close the viewer */
  onClose: () => void
}

function PromptViewer({ agentId, agentName, open, onClose }: PromptViewerProps) {
  // Data state
  const [agent, setAgent] = useState<PromptInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // UI state
  const [viewMode, setViewMode] = useState<ViewMode>('base')
  const [selectedModId, setSelectedModId] = useState<string | null>(null)
  const [combinedPrompt, setCombinedPrompt] = useState<string | null>(null)
  const [loadingCombined, setLoadingCombined] = useState(false)
  const [copied, setCopied] = useState(false)

  // Load agent data
  useEffect(() => {
    if (!open || !agentId) return

    async function loadAgent() {
      setLoading(true)
      setError(null)
      setAgent(null)
      setSelectedModId(null)
      setViewMode('base')
      setCombinedPrompt(null)

      try {
        const catalog = await fetchPromptCatalog()
        // Find the agent in the catalog
        let foundAgent: PromptInfo | null = null
        for (const cat of catalog.categories) {
          const agentData = cat.agents.find((a) => a.agent_id === agentId)
          if (agentData) {
            foundAgent = agentData
            break
          }
        }

        if (foundAgent) {
          setAgent(foundAgent)
          // Auto-select first MOD if available
          if (foundAgent.has_mod_rules && Object.keys(foundAgent.mod_rules).length > 0) {
            const firstMod = Object.keys(foundAgent.mod_rules)[0]
            setSelectedModId(firstMod)
          }
        } else {
          setError(`Agent "${agentId}" not found in catalog`)
        }
      } catch (err) {
        logger.error('Failed to load agent data', err as Error, { component: 'PromptViewer' })
        setError('Failed to load agent data')
      } finally {
        setLoading(false)
      }
    }

    loadAgent()
  }, [open, agentId])

  // Load combined prompt when needed
  useEffect(() => {
    if (viewMode === 'combined' && agentId && selectedModId && agent?.has_mod_rules) {
      setLoadingCombined(true)
      fetchCombinedPrompt(agentId, selectedModId)
        .then(setCombinedPrompt)
        .catch((err) => {
          logger.error('Failed to fetch combined prompt', err as Error, { component: 'PromptViewer' })
          setCombinedPrompt(null)
        })
        .finally(() => setLoadingCombined(false))
    }
  }, [viewMode, agentId, selectedModId, agent])

  // Get prompt content based on view mode
  const getDisplayContent = (): string => {
    if (!agent) return ''

    if (viewMode === 'base') {
      return agent.base_prompt
    }

    if (viewMode === 'mod' && selectedModId && agent.mod_rules[selectedModId]) {
      return agent.mod_rules[selectedModId].content
    }

    if (viewMode === 'combined' && combinedPrompt) {
      return combinedPrompt
    }

    if (viewMode === 'combined' && loadingCombined) {
      return 'Loading combined prompt...'
    }

    return agent.base_prompt
  }

  // Copy prompt to clipboard
  const handleCopy = async () => {
    const content = getDisplayContent()
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      logger.error('Failed to copy to clipboard', err as Error, { component: 'PromptViewer' })
    }
  }

  // Handle MOD selection change
  const handleModChange = (modId: string | null) => {
    setSelectedModId(modId)
    setCombinedPrompt(null) // Reset combined prompt when MOD changes
    if (!modId) {
      setViewMode('base')
    }
  }

  // Handle view mode change
  const handleViewModeChange = (_: React.MouseEvent<HTMLElement>, newMode: ViewMode | null) => {
    if (newMode) {
      setViewMode(newMode)
    }
  }

  return (
    <Slide direction="right" in={open} mountOnEnter unmountOnExit>
      <ViewerContainer elevation={8}>
        <ViewerHeader>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 600, fontSize: '1rem' }}>
              {agentName} Prompts
            </Typography>
            <Typography variant="caption" color="text.secondary">
              View the base prompt and MOD-specific instructions
            </Typography>
          </Box>
          <Tooltip title={copied ? 'Copied!' : 'Copy prompt'}>
            <IconButton onClick={handleCopy} size="small" disabled={loading || !!error}>
              {copied ? <CheckIcon fontSize="small" color="success" /> : <ContentCopyIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </ViewerHeader>

        <ViewerContent>
          {loading && (
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1 }}>
              <CircularProgress />
            </Box>
          )}

          {error && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {error}
            </Alert>
          )}

          {!loading && !error && agent && (
            <>
              {/* Agent Description */}
              <Typography variant="body2" color="text.secondary">
                {agent.description}
              </Typography>

              {/* Tools */}
              {agent.tools.length > 0 && (
                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', alignItems: 'center' }}>
                  <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
                    Tools:
                  </Typography>
                  {agent.tools.map((tool) => (
                    <Chip key={tool} label={tool} size="small" variant="outlined" sx={{ height: 20 }} />
                  ))}
                </Box>
              )}

              {/* View Controls */}
              <ControlsRow>
                <ToggleButtonGroup
                  value={viewMode}
                  exclusive
                  onChange={handleViewModeChange}
                  size="small"
                >
                  <ToggleButton value="base">Base Prompt</ToggleButton>
                  <ToggleButton value="mod" disabled={!selectedModId || !agent.has_mod_rules}>
                    MOD Rules
                  </ToggleButton>
                  <ToggleButton value="combined" disabled={!selectedModId || !agent.has_mod_rules}>
                    Combined
                  </ToggleButton>
                </ToggleButtonGroup>

                {agent.has_mod_rules && Object.keys(agent.mod_rules).length > 0 && (
                  <FormControl size="small" sx={{ minWidth: 120 }}>
                    <InputLabel>MOD</InputLabel>
                    <Select
                      value={selectedModId || ''}
                      label="MOD"
                      onChange={(e) => handleModChange(e.target.value || null)}
                    >
                      {Object.keys(agent.mod_rules).map((modId) => (
                        <MenuItem key={modId} value={modId}>
                          {modId.toUpperCase()}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                )}

                {!agent.has_mod_rules && (
                  <Typography variant="caption" color="text.secondary">
                    No MOD-specific rules for this agent
                  </Typography>
                )}
              </ControlsRow>

              {/* Prompt Content */}
              <PromptContent elevation={0}>
                {getDisplayContent()}
              </PromptContent>

              {/* Version Info */}
              {agent.prompt_version && (
                <Typography variant="caption" color="text.secondary">
                  Version: {agent.prompt_version}
                  {agent.source_file && ` • Source: ${agent.source_file}`}
                </Typography>
              )}
            </>
          )}
        </ViewerContent>
      </ViewerContainer>
    </Slide>
  )
}

export default PromptViewer
