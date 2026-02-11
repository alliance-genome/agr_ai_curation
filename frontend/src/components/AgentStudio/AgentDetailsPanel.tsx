/**
 * AgentDetailsPanel Component
 *
 * Displays detailed information about a selected agent with 3 tabs:
 * - Overview: Summary, capabilities with examples, data sources
 * - Guidance: Limitations, best practices, common issues
 * - Prompts: Base prompt viewer, MOD selector, Combined view
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
  ToggleButton,
  ToggleButtonGroup,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  IconButton,
  Tooltip,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Card,
  CardContent,
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

import { fetchCombinedPrompt } from '@/services/agentStudioService'
import type { PromptInfo } from '@/types/promptExplorer'
import ToolDetailsDialog from './ToolDetailsDialog'

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

type TabValue = 'overview' | 'guidance' | 'prompts'

interface AgentDetailsPanelProps {
  agent: PromptInfo | null
  selectedModId: string | null
  viewMode: 'base' | 'mod' | 'combined'
  onModSelect: (modId: string | null) => void
  onViewModeChange: (mode: 'base' | 'mod' | 'combined') => void
  onDiscussWithClaude?: (agentId: string, agentName: string) => void
  onCloneToWorkshop?: (agentId: string) => void
}

function AgentDetailsPanel({
  agent,
  selectedModId,
  viewMode,
  onModSelect,
  onViewModeChange,
  onDiscussWithClaude,
  onCloneToWorkshop,
}: AgentDetailsPanelProps) {
  const [activeTab, setActiveTab] = useState<TabValue>('overview')
  const [combinedPrompt, setCombinedPrompt] = useState<string | null>(null)
  const [loadingCombined, setLoadingCombined] = useState(false)
  const [selectedTool, setSelectedTool] = useState<string | null>(null)

  // Load combined prompt when needed
  useEffect(() => {
    if (viewMode === 'combined' && agent && selectedModId && agent.has_mod_rules) {
      setLoadingCombined(true)
      fetchCombinedPrompt(agent.agent_id, selectedModId)
        .then(setCombinedPrompt)
        .catch((err) => {
          console.error('Failed to fetch combined prompt:', err)
          setCombinedPrompt(null)
        })
        .finally(() => setLoadingCombined(false))
    }
  }, [viewMode, agent, selectedModId])

  // Handle tab change
  const handleTabChange = (_: React.SyntheticEvent, newValue: TabValue) => {
    setActiveTab(newValue)
  }

  // Get prompt content based on view mode
  const getPromptContent = (): string => {
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
  const handleCopy = () => {
    const content = getPromptContent()
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
  const canCloneToWorkshop = !agent.agent_id.startsWith('ca_')

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
                                "{cap.example_query}"
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
                  Check the Prompts tab to view the agent's prompt instructions.
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
                {agent.has_mod_rules && (
                  <ListItem sx={{ pl: 0 }}>
                    <ListItemIcon sx={{ minWidth: 28 }}>
                      <LightbulbOutlinedIcon fontSize="small" sx={{ color: 'info.main', fontSize: '1rem' }} />
                    </ListItemIcon>
                    <ListItemText
                      primary="This agent has MOD-specific rules - check the Prompts tab to see how behavior varies by species"
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

        {/* Prompts Tab */}
        {activeTab === 'prompts' && (
          <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 2 }}>
            {/* View Mode Controls */}
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
              {agent.has_mod_rules && (
                <>
                  <ToggleButtonGroup
                    value={viewMode}
                    exclusive
                    onChange={(_, val) => val && onViewModeChange(val)}
                    size="small"
                  >
                    <ToggleButton value="base">Base</ToggleButton>
                    <ToggleButton value="mod" disabled={!selectedModId}>
                      MOD Only
                    </ToggleButton>
                    <ToggleButton value="combined" disabled={!selectedModId}>
                      Combined
                    </ToggleButton>
                  </ToggleButtonGroup>

                  <FormControl size="small" sx={{ minWidth: 120 }}>
                    <InputLabel>MOD</InputLabel>
                    <Select
                      value={selectedModId || ''}
                      label="MOD"
                      onChange={(e) => onModSelect(e.target.value || null)}
                    >
                      <MenuItem value="">
                        <em>None</em>
                      </MenuItem>
                      {Object.keys(agent.mod_rules).map((modId) => (
                        <MenuItem key={modId} value={modId}>
                          {modId}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </>
              )}

              <Box sx={{ ml: 'auto' }}>
                <Tooltip title="Copy prompt">
                  <IconButton onClick={handleCopy} size="small">
                    <ContentCopyIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>

            {/* Prompt Content */}
            <PromptContent elevation={0}>
              {getPromptContent()}
            </PromptContent>

            {/* Source File */}
            <Typography variant="caption" color="text.secondary">
              Source: {agent.source_file}
            </Typography>
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
