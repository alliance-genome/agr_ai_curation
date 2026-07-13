/**
 * PromptViewer Component
 *
 * Slide-over panel that displays the canonical ordered prompt layers for an
 * agent. Covers the flow canvas when open.
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

import type { CombinedPromptResponse, PromptInfo, PromptLayerInfo } from '@/types/promptExplorer'
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

type ViewMode = string | 'combined'

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
  const [viewMode, setViewMode] = useState<ViewMode>('combined')
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null)
  const [combinedPrompt, setCombinedPrompt] = useState<CombinedPromptResponse | null>(null)
  const [combinedPromptError, setCombinedPromptError] = useState<string | null>(null)
  const [loadingCombined, setLoadingCombined] = useState(false)
  const [copied, setCopied] = useState(false)

  // Load agent data
  useEffect(() => {
    if (!open || !agentId) return

    async function loadAgent() {
      setLoading(true)
      setError(null)
      setAgent(null)
      setSelectedGroupId(null)
      setViewMode('combined')
      setCombinedPrompt(null)
      setCombinedPromptError(null)

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
          const firstLayer = foundAgent.prompt_layers?.[0]
          setViewMode(firstLayer?.id || 'combined')
          // Auto-select first group if available
          if (foundAgent.has_group_rules && Object.keys(foundAgent.group_rules).length > 0) {
            const firstGroup = Object.keys(foundAgent.group_rules)[0]
            setSelectedGroupId(firstGroup)
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

  // Load the selected group's canonical effective bundle, including its layers.
  useEffect(() => {
    if (agentId && selectedGroupId && agent?.has_group_rules) {
      setLoadingCombined(true)
      setCombinedPromptError(null)
      fetchCombinedPrompt(agentId, selectedGroupId)
        .then((result) => {
          setCombinedPrompt(result)
          setViewMode((currentMode) => {
            if (currentMode === 'combined') return currentMode
            const selectedKind = agent.prompt_layers?.find((layer) => layer.id === currentMode)?.kind
            return result.layer_manifest.layers.find((layer) => layer.kind === selectedKind)?.id
              || result.layer_manifest.layers[0]?.id
              || 'combined'
          })
        })
        .catch((err) => {
          logger.error('Failed to fetch combined prompt', err as Error, { component: 'PromptViewer' })
          setCombinedPrompt(null)
          setCombinedPromptError('Failed to load the selected group prompt layers.')
        })
        .finally(() => setLoadingCombined(false))
    }
  }, [agentId, selectedGroupId, agent])

  const promptLayers: PromptLayerInfo[] = selectedGroupId && combinedPrompt
    ? combinedPrompt.layer_manifest.layers
    : agent?.prompt_layers || []

  const selectedLayer = promptLayers.find((layer) => layer.id === viewMode)

  // Get prompt content based on view mode
  const getDisplayContent = (): string => {
    if (selectedLayer) {
      return selectedLayer.content
    }

    if (viewMode === 'combined' && loadingCombined && selectedGroupId) {
      return 'Loading combined prompt...'
    }

    if (viewMode === 'combined' && combinedPromptError && selectedGroupId) {
      return ''
    }

    if (viewMode === 'combined') {
      return combinedPrompt?.combined_prompt
        || promptLayers.map((layer) => layer.content).filter(Boolean).join('\n\n')
    }

    return ''
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

  // Handle group selection change
  const handleGroupChange = (groupId: string | null) => {
    setSelectedGroupId(groupId)
    setCombinedPrompt(null) // Reset combined prompt when group changes
    setCombinedPromptError(null)
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
              Inspect prompt layers in combined runtime order
            </Typography>
          </Box>
          <Tooltip title={copied ? 'Copied!' : 'Copy prompt'}>
            <IconButton
              onClick={handleCopy}
              size="small"
              disabled={loading || !!error || (viewMode === 'combined' && !!combinedPromptError)}
            >
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

              {agent.prompt_layer_error && <Alert severity="warning">{agent.prompt_layer_error}</Alert>}
              {combinedPromptError && <Alert severity="error">{combinedPromptError}</Alert>}

              {/* Layer Controls */}
              <ControlsRow>
                <ToggleButtonGroup
                  value={viewMode}
                  exclusive
                  onChange={handleViewModeChange}
                  size="small"
                >
                  {promptLayers.map((layer) => (
                    <ToggleButton key={layer.id} value={layer.id}>
                      {layer.title}
                    </ToggleButton>
                  ))}
                  <ToggleButton value="combined">Combined</ToggleButton>
                </ToggleButtonGroup>

                {agent.has_group_rules && Object.keys(agent.group_rules).length > 0 && (
                  <FormControl size="small" sx={{ minWidth: 120 }}>
                    <InputLabel>Group</InputLabel>
                    <Select
                      value={selectedGroupId || ''}
                      label="Group"
                      onChange={(e) => handleGroupChange(e.target.value || null)}
                    >
                      {Object.keys(agent.group_rules).map((groupId) => (
                        <MenuItem key={groupId} value={groupId}>
                          {groupId.toUpperCase()}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                )}

                {!agent.has_group_rules && (
                  <Typography variant="caption" color="text.secondary">
                    No group-specific rules for this agent
                  </Typography>
                )}
              </ControlsRow>

              {selectedLayer && (
                <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', alignItems: 'center' }}>
                  <Chip label={selectedLayer.kind.replaceAll('_', ' ')} size="small" />
                  <Chip
                    label={selectedLayer.editable && !selectedLayer.locked ? 'Editable' : 'Read only'}
                    size="small"
                    color={selectedLayer.editable && !selectedLayer.locked ? 'success' : 'default'}
                    variant="outlined"
                  />
                  {selectedLayer.locked && <Chip label="Locked" size="small" variant="outlined" />}
                  <Typography variant="caption" color="text.secondary">
                    Owner: {selectedLayer.provenance} • Source: {selectedLayer.source_ref}
                  </Typography>
                </Box>
              )}

              {viewMode === 'combined' && !combinedPromptError && (
                <Typography variant="caption" color="text.secondary">
                  {promptLayers.length} layers shown in runtime order
                </Typography>
              )}

              {promptLayers.length === 0 && !agent.prompt_layer_error && (
                <Alert severity="warning">No canonical prompt layers are available for this agent.</Alert>
              )}

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
