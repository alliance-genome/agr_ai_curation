/**
 * AgentDetailsPanel Component
 *
 * Displays detailed information about a selected agent with 3 tabs:
 * - Overview: Summary, capabilities with examples, data sources
 * - Guidance: Limitations, best practices, common issues
 * - Prompts: Base prompt viewer, group selector, combined view
 *
 * Also includes a "Discuss with Claude" button for context-aware help.
 */

import { useState, useEffect } from 'react'
import {
  Box,
  Typography,
  Tabs,
  Tab,
  Paper,
  Chip,
  Button,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  IconButton,
  Tooltip,
  Stack,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Card,
  CardContent,
  Alert,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import ChatIcon from '@mui/icons-material/Chat'
import ScienceIcon from '@mui/icons-material/Science'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import StorageIcon from '@mui/icons-material/Storage'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import LightbulbOutlinedIcon from '@mui/icons-material/LightbulbOutlined'
import HelpOutlineIcon from '@mui/icons-material/HelpOutline'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'

import { fetchCombinedPrompt } from '@/services/agentStudioService'
import type { PromptInfo, PromptLayerInfo } from '@/types/promptExplorer'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import ToolDetailsDialog from './ToolDetailsDialog'
import DomainEnvelopeMetadataPanel from './DomainEnvelopeMetadataPanel'

// Styled components
const PanelContainer = styled(Box)(() => ({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  overflow: 'hidden',
}))

const PanelHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderBottom: `1px solid ${theme.palette.divider}`,
}))

const HeaderContent = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  gap: theme.spacing(2),
}))

const StyledTabs = styled(Tabs)(() => ({
  minHeight: 36,
  '& .MuiTabs-indicator': {
    height: 2,
  },
}))

const StyledTab = styled(Tab)(({ theme }) => ({
  minHeight: 36,
  textTransform: 'none',
  fontWeight: 500,
  fontSize: '0.8rem',
  padding: theme.spacing(0.5, 2),
}))

const TabContent = styled(Box)(({ theme }) => ({
  flex: 1,
  overflow: 'auto',
  padding: theme.spacing(2),
}))

const CapabilityCard = styled(Card)(({ theme }) => ({
  marginBottom: theme.spacing(1.5),
  backgroundColor: alpha(theme.palette.background.default, 0.5),
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.05),
  },
}))

const ExampleBox = styled(Box)(({ theme }) => ({
  backgroundColor: alpha(theme.palette.info.main, 0.08),
  borderRadius: theme.shape.borderRadius,
  padding: theme.spacing(1),
  marginTop: theme.spacing(0.5),
  fontFamily: 'monospace',
  fontSize: '0.8rem',
}))

const DataSourceCard = styled(Card)(({ theme }) => ({
  marginBottom: theme.spacing(1),
  backgroundColor: alpha(theme.palette.success.main, 0.05),
}))

const LimitationItem = styled(ListItem)(() => ({
  paddingLeft: 0,
  alignItems: 'flex-start',
}))

const PromptContent = styled(Paper)(({ theme }) => ({
  padding: theme.spacing(2),
  backgroundColor: alpha(theme.palette.background.default, 0.5),
  fontFamily: 'monospace',
  fontSize: '0.8rem',
  lineHeight: 1.6,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  flex: 1,
  overflow: 'auto',
}))

const SectionTitle = styled(Typography)(({ theme }) => ({
  fontWeight: 600,
  fontSize: '0.9rem',
  marginBottom: theme.spacing(1.5),
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
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

type TabValue = 'overview' | 'guidance' | 'envelope' | 'prompts'

interface AgentDetailsPanelProps {
  agent: PromptInfo | null
  selectedGroupId: string | null
  onGroupSelect: (groupId: string | null) => void
  onDiscussWithClaude?: (agentId: string, agentName: string) => void
  onCloneToWorkshop?: (agentId: string) => void
}

function AgentDetailsPanel({
  agent,
  selectedGroupId,
  onGroupSelect,
  onDiscussWithClaude,
  onCloneToWorkshop,
}: AgentDetailsPanelProps) {
  const { agents: agentMetadata } = useAgentMetadata()
  const [activeTab, setActiveTab] = useState<TabValue>('overview')
  const [combinedPrompt, setCombinedPrompt] = useState<string | null>(null)
  const [loadingCombined, setLoadingCombined] = useState(false)
  const [selectedTool, setSelectedTool] = useState<string | null>(null)
  const domainEnvelopeMetadata = agent
    ? agentMetadata[agent.agent_id]?.domain_envelope
    : undefined

  // Load combined prompt when needed
  useEffect(() => {
    if (agent?.custom_prompt_overlay_status === 'needs_review') {
      setCombinedPrompt(null)
      setLoadingCombined(false)
      return
    }
    if (agent && selectedGroupId && agent.has_group_rules) {
      setLoadingCombined(true)
      fetchCombinedPrompt(agent.agent_id, selectedGroupId)
        .then(setCombinedPrompt)
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
      setActiveTab('overview')
    }
  }, [activeTab, domainEnvelopeMetadata])

  // Handle tab change
  const handleTabChange = (_: React.SyntheticEvent, newValue: TabValue) => {
    setActiveTab(newValue)
  }

  const overlayNeedsReview = agent?.custom_prompt_overlay_status === 'needs_review'
  const getPromptLayerPreview = (): string => {
    return agent?.prompt_layers && agent.prompt_layers.length > 0
      ? agent.prompt_layers.map((layer) => layer.content).filter(Boolean).join('\n\n')
      : ''
  }

  // Copy prompt to clipboard
  const handleCopy = () => {
    const layerPreview = getPromptLayerPreview()
    const reviewMessage = 'Curator overlay needs coordinator review before it can be included in the effective prompt.'
    const content = agent && selectedGroupId && agent.has_group_rules
      ? (combinedPrompt || (overlayNeedsReview ? (layerPreview || reviewMessage) : agent.base_prompt))
      : (layerPreview || (overlayNeedsReview ? reviewMessage : agent?.base_prompt || ''))
    navigator.clipboard.writeText(content).catch((err) => {
      console.error('Failed to copy:', err)
    })
  }

  // Handle discuss with Claude
  const handleDiscuss = () => {
    if (agent && onDiscussWithClaude) {
      onDiscussWithClaude(agent.agent_id, agent.agent_name)
    }
  }

  const handleCloneToWorkshop = () => {
    if (agent && onCloneToWorkshop) {
      onCloneToWorkshop(agent.agent_id)
    }
  }

  // Empty state when no agent selected
  if (!agent) {
    return (
      <EmptyState>
        <Box>
          <HelpOutlineIcon sx={{ fontSize: 48, mb: 2, opacity: 0.5 }} />
          <Typography variant="body1">
            Select an agent from the list to view its details.
          </Typography>
        </Box>
      </EmptyState>
    )
  }

  const { documentation } = agent
  const canCloneToWorkshop = agent.agent_id !== 'task_input'
  const promptLayers = agent.prompt_layers || []
  const layersByKind = promptLayers.reduce<Record<string, PromptLayerInfo[]>>((acc, layer) => {
    acc[layer.kind] = [...(acc[layer.kind] || []), layer]
    return acc
  }, {})
  const coreLayers = layersByKind.core_static || []
  const generatedLayers = layersByKind.core_generated || []
  const baseLayers = layersByKind.base_prompt || []
  const overlayLayers = layersByKind.curator_overlay || []
  const layerPreview = getPromptLayerPreview()
  const selectedGroupRule = selectedGroupId ? agent.group_rules[selectedGroupId] : undefined
  const overlayReviewMessage = agent.custom_prompt_warning
    || 'Curator overlay needs coordinator review before it can be included in the effective prompt.'
  const promptLayerError = agent.prompt_layer_error
  const effectivePromptPreview = selectedGroupId && agent.has_group_rules
    ? (combinedPrompt || (loadingCombined
      ? 'Loading effective prompt preview...'
      : (overlayNeedsReview ? (layerPreview || overlayReviewMessage) : agent.base_prompt)))
    : (overlayNeedsReview
      ? (layerPreview || overlayReviewMessage)
      : promptLayers.length > 0
      ? promptLayers.map((layer) => layer.content).filter(Boolean).join('\n\n')
      : agent.base_prompt)

  const renderLayerSection = (
    title: string,
    layers: PromptLayerInfo[],
    displayContent = '',
    options: { locked?: boolean; editable?: boolean; emptyText?: string } = {}
  ) => {
    const hasLayers = layers.length > 0
    const locked = options.locked ?? (hasLayers ? layers.every((layer) => layer.locked) : false)
    const editable = options.editable ?? (hasLayers ? layers.some((layer) => layer.editable) : false)
    const content = hasLayers
      ? layers.map((layer) => layer.content).filter(Boolean).join('\n\n')
      : displayContent
    const source = hasLayers
      ? layers.map((layer) => layer.provenance).filter(Boolean).join(', ')
      : agent.source_file

    return (
      <Box>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            {title}
          </Typography>
          <Chip
            size="small"
            icon={locked ? <LockOutlinedIcon /> : <EditOutlinedIcon />}
            label={locked ? 'Read-only' : (editable ? 'Editable' : 'Context')}
            color={locked ? 'default' : 'primary'}
            variant="outlined"
            sx={{ height: 22, fontSize: '0.7rem' }}
          />
        </Stack>
        <PromptContent elevation={0}>
          {content || options.emptyText || 'No content for this layer.'}
        </PromptContent>
        {source && (
          <Typography variant="caption" color="text.secondary">
            Source: {source}
          </Typography>
        )}
      </Box>
    )
  }

  return (
    <PanelContainer>
      {/* Header */}
      <PanelHeader>
        <HeaderContent>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 600, mb: 0.5 }}>
              {agent.agent_name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {documentation?.summary || agent.description}
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            {canCloneToWorkshop && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<ScienceIcon />}
                onClick={handleCloneToWorkshop}
                sx={{ whiteSpace: 'nowrap' }}
              >
                Clone to Workshop
              </Button>
            )}
            <Button
              variant="outlined"
              size="small"
              startIcon={<ChatIcon />}
              onClick={handleDiscuss}
              sx={{ whiteSpace: 'nowrap' }}
            >
              Discuss with Claude
            </Button>
          </Box>
        </HeaderContent>

        {/* Tools chips - clickable for details */}
        {agent.tools.length > 0 && (
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 1.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5, alignSelf: 'center' }}>
              Tools:
            </Typography>
            {agent.tools.map((tool) => (
              <Tooltip key={tool} title="Click to view tool details">
                <Chip
                  label={tool}
                  size="small"
                  variant="outlined"
                  onClick={() => setSelectedTool(tool)}
                  sx={{
                    cursor: 'pointer',
                    '&:hover': {
                      backgroundColor: 'action.hover',
                      borderColor: 'primary.main',
                    },
                  }}
                />
              </Tooltip>
            ))}
          </Box>
        )}
      </PanelHeader>

      {/* Tabs */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider', px: 2 }}>
        <StyledTabs value={activeTab} onChange={handleTabChange}>
          <StyledTab label="Overview" value="overview" />
          <StyledTab label="Guidance" value="guidance" />
          {domainEnvelopeMetadata && (
            <StyledTab label="Envelope" value="envelope" />
          )}
          <StyledTab label="Prompts" value="prompts" />
        </StyledTabs>
      </Box>

      {/* Tab Content */}
      <TabContent>
        {/* Overview Tab */}
        {activeTab === 'overview' && (
          <Box>
            {/* Capabilities */}
            {documentation?.capabilities && documentation.capabilities.length > 0 && (
              <Box sx={{ mb: 3 }}>
                <SectionTitle>
                  <CheckCircleOutlineIcon fontSize="small" color="success" />
                  Capabilities
                </SectionTitle>
                {documentation.capabilities.map((cap, idx) => (
                  <CapabilityCard key={idx} variant="outlined">
                    <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                        {cap.name}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                        {cap.description}
                      </Typography>
                      {(cap.example_query || cap.example_result) && (
                        <ExampleBox>
                          {cap.example_query && (
                            <Box>
                              <Typography variant="caption" color="text.secondary">
                                Example query:
                              </Typography>
                              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                                {cap.example_query}
                              </Typography>
                            </Box>
                          )}
                          {cap.example_result && (
                            <Box sx={{ mt: 0.5 }}>
                              <Typography variant="caption" color="text.secondary">
                                Result:
                              </Typography>
                              <Typography variant="body2" sx={{ color: 'success.main' }}>
                                → {cap.example_result}
                              </Typography>
                            </Box>
                          )}
                        </ExampleBox>
                      )}
                    </CardContent>
                  </CapabilityCard>
                ))}
              </Box>
            )}

            {/* Data Sources */}
            {documentation?.data_sources && documentation.data_sources.length > 0 && (
              <Box sx={{ mb: 3 }}>
                <SectionTitle>
                  <StorageIcon fontSize="small" color="primary" />
                  Data Sources
                </SectionTitle>
                {documentation.data_sources.map((source, idx) => (
                  <DataSourceCard key={idx} variant="outlined">
                    <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                        {source.name}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                        {source.description}
                      </Typography>
                      {source.species_supported && source.species_supported.length > 0 && (
                        <Box sx={{ mt: 1, display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                          <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
                            Species:
                          </Typography>
                          {source.species_supported.map((sp) => (
                            <Chip key={sp} label={sp} size="small" variant="filled" color="primary" sx={{ height: 20, fontSize: '0.7rem' }} />
                          ))}
                        </Box>
                      )}
                      {source.data_types && source.data_types.length > 0 && (
                        <Box sx={{ mt: 0.5, display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                          <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
                            Data types:
                          </Typography>
                          {source.data_types.map((dt) => (
                            <Chip key={dt} label={dt} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                          ))}
                        </Box>
                      )}
                    </CardContent>
                  </DataSourceCard>
                ))}
              </Box>
            )}

            {/* Empty state for Overview */}
            {(!documentation?.capabilities || documentation.capabilities.length === 0) &&
             (!documentation?.data_sources || documentation.data_sources.length === 0) && (
              <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                <Typography variant="body2">
                  No detailed documentation available for this agent yet.
                </Typography>
                <Typography variant="caption" sx={{ mt: 1, display: 'block' }}>
                  Check the Prompts tab to view the agent prompt instructions.
                </Typography>
              </Box>
            )}
          </Box>
        )}

        {/* Guidance Tab */}
        {activeTab === 'guidance' && (
          <Box>
            {/* Limitations */}
            {documentation?.limitations && documentation.limitations.length > 0 && (
              <Box sx={{ mb: 3 }}>
                <SectionTitle>
                  <WarningAmberIcon fontSize="small" color="warning" />
                  Known Limitations
                </SectionTitle>
                <List dense disablePadding>
                  {documentation.limitations.map((limitation, idx) => (
                    <LimitationItem key={idx}>
                      <ListItemIcon sx={{ minWidth: 28, mt: 0.5 }}>
                        <WarningAmberIcon fontSize="small" sx={{ color: 'warning.main', fontSize: '1rem' }} />
                      </ListItemIcon>
                      <ListItemText
                        primary={limitation}
                        primaryTypographyProps={{ variant: 'body2' }}
                      />
                    </LimitationItem>
                  ))}
                </List>
              </Box>
            )}

            {/* Tips section - static for now */}
            <Box sx={{ mb: 3 }}>
              <SectionTitle>
                <LightbulbOutlinedIcon fontSize="small" color="info" />
                Tips for Best Results
              </SectionTitle>
              <List dense disablePadding>
                <ListItem sx={{ pl: 0 }}>
                  <ListItemIcon sx={{ minWidth: 28 }}>
                    <LightbulbOutlinedIcon fontSize="small" sx={{ color: 'info.main', fontSize: '1rem' }} />
                  </ListItemIcon>
                  <ListItemText
                    primary="Be specific with your queries - include gene symbols, IDs, or species when possible"
                    primaryTypographyProps={{ variant: 'body2' }}
                  />
                </ListItem>
                <ListItem sx={{ pl: 0 }}>
                  <ListItemIcon sx={{ minWidth: 28 }}>
                    <LightbulbOutlinedIcon fontSize="small" sx={{ color: 'info.main', fontSize: '1rem' }} />
                  </ListItemIcon>
                  <ListItemText
                    primary="Use the 'Discuss with Claude' button if you're unsure how this agent can help"
                    primaryTypographyProps={{ variant: 'body2' }}
                  />
                </ListItem>
                {agent.has_group_rules && (
                  <ListItem sx={{ pl: 0 }}>
                    <ListItemIcon sx={{ minWidth: 28 }}>
                      <LightbulbOutlinedIcon fontSize="small" sx={{ color: 'info.main', fontSize: '1rem' }} />
                    </ListItemIcon>
                    <ListItemText
                      primary="This agent has group-specific rules - check the Prompts tab to see how behavior varies by species"
                      primaryTypographyProps={{ variant: 'body2' }}
                    />
                  </ListItem>
                )}
              </List>
            </Box>

            {/* Empty state for Guidance */}
            {(!documentation?.limitations || documentation.limitations.length === 0) && (
              <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                <Typography variant="body2">
                  No specific limitations documented for this agent.
                </Typography>
              </Box>
            )}
          </Box>
        )}

        {activeTab === 'envelope' && domainEnvelopeMetadata && (
          <DomainEnvelopeMetadataPanel
            metadata={domainEnvelopeMetadata}
            title="Envelope & Validation"
            validationModeNote="Automatic validation is projected from domain-pack metadata. Flow Builder persists allowed opt-outs, custom validation agents, and validation-agent steering prompts in the flow definition."
          />
        )}

        {/* Prompts Tab */}
        {activeTab === 'prompts' && (
          <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 2 }}>
            {/* Group selector and copy control */}
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
              {agent.has_group_rules && (
                <>
                  <FormControl size="small" sx={{ minWidth: 120 }}>
                    <InputLabel>Group</InputLabel>
                    <Select
                      value={selectedGroupId || ''}
                      label="Group"
                      onChange={(e) => onGroupSelect(e.target.value || null)}
                    >
                      <MenuItem value="">
                        <em>None</em>
                      </MenuItem>
                      {Object.keys(agent.group_rules).map((groupId) => (
                        <MenuItem key={groupId} value={groupId}>
                          {groupId}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </>
              )}

              <Box sx={{ ml: 'auto' }}>
                <Tooltip title="Copy effective prompt preview">
                  <IconButton onClick={handleCopy} size="small">
                    <ContentCopyIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>

            {promptLayerError && (
              <Alert severity="error" variant="outlined">
                {promptLayerError}
              </Alert>
            )}
            {renderLayerSection('Core Prompt', coreLayers, '', {
              locked: true,
              editable: false,
              emptyText: 'No backend-owned core prompt layer was returned for this agent.',
            })}
            {renderLayerSection('Generated Contract', generatedLayers, '', {
              locked: true,
              editable: false,
              emptyText: 'No generated runtime contract layer is required for this agent.',
            })}
            {renderLayerSection('Base Prompt', baseLayers, agent.base_prompt, {
              locked: false,
              editable: true,
            })}
            {renderLayerSection(
              'Group Rules',
              [],
              selectedGroupRule?.content || '',
              {
                locked: false,
                editable: Boolean(selectedGroupRule),
                emptyText: selectedGroupId
                  ? `No group rules were returned for ${selectedGroupId}.`
                  : 'Select a group to view group rules.',
              }
            )}
            {overlayNeedsReview && (
              <Alert severity="warning" variant="outlined">
                {overlayReviewMessage}
              </Alert>
            )}
            {renderLayerSection('Curator Overlay', overlayNeedsReview ? [] : overlayLayers, overlayNeedsReview ? agent.base_prompt : '', {
              locked: false,
              editable: !overlayNeedsReview,
              emptyText: 'No curator overlay is applied.',
            })}
            <Box>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  Effective Prompt Preview
                </Typography>
                {agent.effective_prompt_hash && (
                  <Chip
                    size="small"
                    label={agent.effective_prompt_hash.slice(0, 12)}
                    variant="outlined"
                    sx={{ height: 22, fontSize: '0.7rem' }}
                  />
                )}
              </Stack>
              <PromptContent elevation={0}>
                {effectivePromptPreview}
              </PromptContent>
            </Box>
          </Box>
        )}
      </TabContent>

      {/* Tool Details Dialog */}
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
