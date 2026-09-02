import { useMemo, useState } from 'react'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormHelperText,
  FormControl,
  FormControlLabel,
  IconButton,
  InputAdornment,
  InputLabel,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Select,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import HelpOutlineIcon from '@mui/icons-material/HelpOutline'
import SearchIcon from '@mui/icons-material/Search'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import DeleteIcon from '@mui/icons-material/Delete'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'

import type {
  PromptCatalog,
  CustomAgent,
  AgentWorkshopContext,
  ModelOption,
  ToolIdeaRequest,
  ToolIdeaConversationEntry,
  WorkshopPromptUpdateRequest,
} from '@/types/promptExplorer'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import DomainEnvelopeMetadataPanel from '../DomainEnvelopeMetadataPanel'
import { useWorkshopDraft } from './useWorkshopDraft'
import {
  ALL_GROUPS_VALUE,
  buildDiscussDraftMessage,
  buildDiscussPromptMessage,
  buildModelAdviceMessage,
  buildToolRequestMessage,
  formatReasoningLabel,
  type GettingStartedMode,
} from './workshopDraftUtils'

const Toolbar = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  height: 32,
  minHeight: 32,
  padding: theme.spacing(0, 0.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
  backgroundColor: alpha(theme.palette.background.default, 0.4),
  gap: theme.spacing(0.25),
}))

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

const SectionCard = styled(Paper)(({ theme }) => ({
  padding: theme.spacing(2.5),
  borderRadius: 10,
  border: 'none',
  backgroundColor: alpha(theme.palette.background.paper, 0.45),
}))

const SectionHeader = styled(Typography)(({ theme }) => ({
  fontSize: '0.7rem',
  fontWeight: 700,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: theme.palette.text.secondary,
  paddingLeft: theme.spacing(1.5),
  borderLeft: `3px solid ${theme.palette.primary.main}`,
  marginBottom: theme.spacing(2),
}))

const PromptLayerPreview = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.5),
  borderRadius: 6,
  border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
  backgroundColor: alpha(theme.palette.common.black, 0.12),
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
  fontSize: '0.78rem',
  lineHeight: 1.55,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  maxHeight: 260,
  overflow: 'auto',
}))

const StyledToggleButtonGroup = styled(ToggleButtonGroup)(({ theme }) => ({
  '& .MuiToggleButton-root': {
    textTransform: 'none',
    fontSize: '0.8rem',
    fontWeight: 500,
    padding: theme.spacing(0.5, 1.5),
    border: `1px solid ${theme.palette.divider}`,
    '&.Mui-selected': {
      backgroundColor: alpha(theme.palette.primary.main, 0.12),
      color: theme.palette.primary.main,
      borderColor: alpha(theme.palette.primary.main, 0.4),
      '&:hover': {
        backgroundColor: alpha(theme.palette.primary.main, 0.18),
      },
    },
  },
}))

const StyledAccordion = styled(Accordion)(({ theme }) => ({
  backgroundColor: 'transparent',
  boxShadow: 'none',
  border: `1px solid ${alpha(theme.palette.divider, 0.18)}`,
  borderRadius: `${theme.shape.borderRadius}px !important`,
  '&::before': { display: 'none' },
  '&:not(:last-child)': { marginBottom: theme.spacing(1) },
  '& .MuiAccordionSummary-root': {
    minHeight: 42,
    padding: theme.spacing(0, 1.5),
    '& .MuiAccordionSummary-content': {
      margin: theme.spacing(0.75, 0),
    },
  },
  '& .MuiAccordionDetails-root': {
    padding: theme.spacing(0, 1.5, 1.5),
  },
}))

const ToolbarStatus = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
  marginLeft: 'auto',
  paddingRight: theme.spacing(1),
  color: theme.palette.text.secondary,
  fontSize: '0.75rem',
}))

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

function toolIdeaStatusLabel(status: ToolIdeaRequest['status']): string {
  return status.replace(/_/g, ' ')
}

function toolIdeaStatusColor(
  status: ToolIdeaRequest['status']
): 'default' | 'info' | 'warning' | 'success' | 'error' {
  if (status === 'reviewed') return 'info'
  if (status === 'in_progress') return 'warning'
  if (status === 'completed') return 'success'
  if (status === 'declined') return 'error'
  return 'default'
}

function buildModelHelpText(models: ModelOption[]): string {
  if (models.length === 0) return 'No curator-visible models are currently configured.'

  return [
    'Configured model guidance:',
    ...models.map((model) => {
      const defaultReasoning = model.default_reasoning
        ? ` (default reasoning: ${model.default_reasoning})`
        : ''
      return `• ${model.model_id}${defaultReasoning}: ${model.guidance || model.description || 'No guidance configured.'}`
    }),
  ].join('\n')
}

const REASONING_HELP_TEXT = [
  'Reasoning levels trade off speed and depth:',
  '• low: fastest',
  '• medium: recommended default',
  '• high: slowest, use only for hard ambiguity',
].join('\n')

interface PromptWorkshopProps {
  catalog: PromptCatalog
  initialParentAgentId?: string | null
  initialCustomAgentId?: string | null
  onContextChange?: (context: AgentWorkshopContext) => void
  onVerifyRequest?: (message: string) => void
  opusConversation?: ToolIdeaConversationEntry[]
  incomingPromptUpdate?: WorkshopPromptUpdateRequest | null
}

function PromptWorkshop({
  catalog,
  initialParentAgentId,
  initialCustomAgentId,
  onContextChange,
  onVerifyRequest,
  opusConversation = [],
  incomingPromptUpdate = null,
}: PromptWorkshopProps) {
  const { agents: agentMetadata } = useAgentMetadata()
  const draft = useWorkshopDraft({
    catalog,
    initialParentAgentId,
    initialCustomAgentId,
    onContextChange,
    incomingPromptUpdate,
  })
  const {
    modelOptions,
    toolLibrary,
    templateOptions,
    loading,
    gettingStartedMode,
    parentAgentId,
    setParentAgentId,
    parentAgent,
    selectedTemplate,
    customAgents,
    selectedCustomAgentId,
    selectedCustomAgent,
    selectCustomAgent,
    cloneSourceAgentId,
    setCloneSourceAgentId,
    selectedCloneSource,
    name,
    setName,
    description,
    setDescription,
    icon,
    setIcon,
    iconOptions,
    customPrompt,
    setCustomPrompt,
    groupPromptOverrides,
    includeGroupRules,
    setIncludeGroupRules,
    selectedVisibility,
    setSelectedVisibility,
    selectedAllowedGroupIds,
    setSelectedAllowedGroupIds,
    selectedModelId,
    handleModelChange,
    selectedModelReasoning,
    setSelectedModelReasoning,
    selectedToolIds,
    toggleTool,
    removeTool,
    groupId,
    setGroupId,
    availableGroupIds,
    selectedGroupId,
    selectedGroupPrompt,
    hasSelectedGroupOverride,
    handleSelectedGroupPromptChange,
    handleResetSelectedGroupPrompt,
    loggedInGroupIds,
    loggedInAsLabel,
    currentUserGroupIds,
    inheritedAllowedGroupIds,
    selectableGroupOptions,
    parentCorePrompt,
    parentGeneratedContract,
    parentBasePrompt,
    overlayStatus,
    overlayWarning,
    selectedModelOption,
    selectedModelReasoningDescription,
    domainEnvelopeAgentId,
    toolIdeaRequests,
    toolIdeasLoading,
    submitToolIdea,
    toolIdeaSubmitting,
    versions,
    saving,
    status,
    error,
    setError,
    setStatus,
    handleNew,
    startDraft,
    handleSave,
    handleDeleteById,
    handleRevert,
    selfExclusionPrompt,
    confirmSelfExclusion,
    cancelSelfExclusion,
  } = draft

  const [workshopSection, setWorkshopSection] = useState<'setup' | 'prompt' | 'tools' | 'reference'>('setup')
  const [saveNotes, setSaveNotes] = useState('')
  const [toolIdeaDialogOpen, setToolIdeaDialogOpen] = useState(false)
  const [toolIdeaTitle, setToolIdeaTitle] = useState('')
  const [toolIdeaDescription, setToolIdeaDescription] = useState('')
  const [fileMenuAnchor, setFileMenuAnchor] = useState<HTMLElement | null>(null)
  const [openDialogOpen, setOpenDialogOpen] = useState(false)
  const [openSearchTerm, setOpenSearchTerm] = useState('')
  const [manageDialogOpen, setManageDialogOpen] = useState(false)
  const [toolLibraryDialogOpen, setToolLibraryDialogOpen] = useState(false)
  const [toolLibrarySearch, setToolLibrarySearch] = useState('')
  const [toolLibraryCategory, setToolLibraryCategory] = useState('all')
  const [saveAsDialogOpen, setSaveAsDialogOpen] = useState(false)
  const [saveAsName, setSaveAsName] = useState('')
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [pendingDeleteAgent, setPendingDeleteAgent] = useState<CustomAgent | null>(null)

  const selfExclusionDialogOpen = Boolean(selfExclusionPrompt)
  const loggedInGroupsLabel = loggedInGroupIds.length > 0 ? loggedInGroupIds.join(', ') : 'No matching group detected'
  const hasAnyGroupOverrides = Object.keys(groupPromptOverrides).length > 0
  const domainEnvelopeMetadata = domainEnvelopeAgentId
    ? agentMetadata[domainEnvelopeAgentId]?.domain_envelope
    : undefined

  const filteredOpenAgents = useMemo(() => {
    if (!openSearchTerm.trim()) return customAgents
    const query = openSearchTerm.toLowerCase()
    return customAgents.filter((agent) => {
      return agent.name.toLowerCase().includes(query) || (agent.description || '').toLowerCase().includes(query)
    })
  }, [customAgents, openSearchTerm])

  const toolCategories = useMemo(() => {
    const categories = Array.from(new Set(toolLibrary.map((tool) => tool.category).filter(Boolean)))
    categories.sort((a, b) => a.localeCompare(b))
    return categories
  }, [toolLibrary])

  const filteredToolLibrary = useMemo(() => {
    const query = toolLibrarySearch.trim().toLowerCase()
    return toolLibrary.filter((tool) => {
      const matchesCategory = toolLibraryCategory === 'all' || tool.category === toolLibraryCategory
      const matchesSearch = !query
        || tool.display_name.toLowerCase().includes(query)
        || tool.tool_key.toLowerCase().includes(query)
        || tool.category.toLowerCase().includes(query)
      return matchesCategory && matchesSearch
    })
  }, [toolLibrary, toolLibraryCategory, toolLibrarySearch])

  const saveWithNotes = async (options?: { forceCreate?: boolean; nameOverride?: string }) => {
    await handleSave({ ...options, notes: saveNotes })
    setSaveNotes('')
  }

  const handleRevertWithNotes = async (version: number) => {
    await handleRevert(version)
  }

  const handleOpenToolLibrary = () => {
    setToolLibraryDialogOpen(true)
  }

  const handleCloseToolLibrary = () => {
    setToolLibraryDialogOpen(false)
    setToolLibrarySearch('')
    setToolLibraryCategory('all')
  }

  const handleAskClaudeForTool = () => {
    const targetName = selectedCustomAgent?.name || name.trim() || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
    const targetId = selectedCustomAgent?.agent_id || parentAgentId || 'unsaved_draft'
    onVerifyRequest?.(buildToolRequestMessage(targetName, targetId, selectedToolIds))
    setStatus('Opened tool-ideation discussion with Claude')
  }

  const handleOpenToolIdeaDialog = () => {
    setToolIdeaDialogOpen(true)
  }

  const handleCloseToolIdeaDialog = () => {
    setToolIdeaDialogOpen(false)
    setToolIdeaTitle('')
    setToolIdeaDescription('')
  }

  const handleSubmitToolIdea = async () => {
    const submitted = await submitToolIdea(toolIdeaTitle, toolIdeaDescription, opusConversation)
    if (submitted) handleCloseToolIdeaDialog()
  }

  const handleFileMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setFileMenuAnchor(event.currentTarget)
  }

  const handleFileMenuClose = () => {
    setFileMenuAnchor(null)
  }

  const handleOpenDialogOpen = () => {
    handleFileMenuClose()
    setOpenDialogOpen(true)
  }

  const handleOpenDialogClose = () => {
    setOpenDialogOpen(false)
    setOpenSearchTerm('')
  }

  const handleManageDialogOpen = () => {
    handleFileMenuClose()
    setManageDialogOpen(true)
  }

  const handleManageDialogClose = () => {
    setManageDialogOpen(false)
  }

  const handleSaveAsOpen = () => {
    handleFileMenuClose()
    const suggestedName = (name || selectedCustomAgent?.name || '').trim()
    setSaveAsName(suggestedName ? `${suggestedName} (Copy)` : '')
    setSaveAsDialogOpen(true)
  }

  const handleSaveAsClose = () => {
    setSaveAsDialogOpen(false)
    setSaveAsName('')
  }

  const handleSaveAsConfirm = async () => {
    const trimmedName = saveAsName.trim()
    if (!trimmedName) {
      setError('Please enter a custom agent name')
      return
    }
    setSaveAsDialogOpen(false)
    await saveWithNotes({ forceCreate: true, nameOverride: trimmedName })
    setSaveAsName('')
  }

  const requestDelete = (agent?: CustomAgent) => {
    const target = agent || selectedCustomAgent
    if (!target) return
    handleFileMenuClose()
    setPendingDeleteAgent(target)
    setDeleteConfirmOpen(true)
  }

  const handleDeleteCancel = () => {
    setDeleteConfirmOpen(false)
    setPendingDeleteAgent(null)
  }

  const handleDeleteConfirm = async () => {
    if (!pendingDeleteAgent) return
    const target = pendingDeleteAgent
    setDeleteConfirmOpen(false)
    setPendingDeleteAgent(null)
    await handleDeleteById(target)
  }

  const handleDiscussWithClaude = () => {
    const targetName = selectedCustomAgent?.name || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
    const targetId = selectedCustomAgent?.agent_id || parentAgentId || 'unknown'
    onVerifyRequest?.(buildDiscussDraftMessage(targetName, targetId, selectedGroupId))
  }

  const handleAskClaudeAboutModels = () => {
    const targetName = selectedCustomAgent?.name || name.trim() || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
    onVerifyRequest?.(buildModelAdviceMessage(targetName, modelOptions, selectedModelId, selectedModelReasoning, selectedToolIds))
    setStatus('Opened model-selection discussion with Claude')
  }

  const handleDiscussPromptChangesWithClaude = () => {
    const targetName = selectedCustomAgent?.name || name.trim() || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
    const targetId = selectedCustomAgent?.agent_id || parentAgentId || 'unknown'
    onVerifyRequest?.(buildDiscussPromptMessage(targetName, targetId, selectedGroupId))
    setStatus('Opened system-prompt discussion with Claude')
  }

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Toolbar>
        <MenuTrigger onClick={handleFileMenuOpen}>File</MenuTrigger>
        <StyledMenu
          anchorEl={fileMenuAnchor}
          open={Boolean(fileMenuAnchor)}
          onClose={handleFileMenuClose}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <StyledMenuItem
            onClick={() => {
              handleFileMenuClose()
              handleNew()
            }}
          >
            <span>New Agent</span>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleOpenDialogOpen}>
            <span>Open Agent...</span>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleManageDialogOpen}>
            <span>Manage Agents...</span>
          </StyledMenuItem>
          <Divider />
          <StyledMenuItem onClick={() => void saveWithNotes()} disabled={saving}>
            <span>{selectedCustomAgentId ? 'Save Agent' : 'Save New Agent'}</span>
          </StyledMenuItem>
          <StyledMenuItem onClick={handleSaveAsOpen} disabled={saving}>
            <span>Save Agent As...</span>
          </StyledMenuItem>
          <StyledMenuItem onClick={() => requestDelete()} disabled={!selectedCustomAgentId || saving}>
            <span>Delete Agent</span>
          </StyledMenuItem>
        </StyledMenu>

        {/* Discuss with Claude Button */}
        {onVerifyRequest && (
          <Button
            onClick={handleDiscussWithClaude}
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
            Discuss with Claude
          </Button>
        )}

        <ToolbarStatus>
          <Typography variant="caption" color="text.secondary">
            {selectedCustomAgent ? `Editing: ${selectedCustomAgent.name}` : 'Editing: New draft'}
          </Typography>
          {(loading || saving) && <CircularProgress size={16} />}
        </ToolbarStatus>
      </Toolbar>

      {/* ── Fixed header region (does not scroll): identity crown + section nav ── */}
      <Box
        sx={{
          flexShrink: 0,
          px: 2.5,
          pt: 2,
          pb: 1.5,
          backgroundColor: 'background.paper',
          borderBottom: (theme) => `1px solid ${alpha(theme.palette.divider, 0.25)}`,
          boxShadow: (theme) => `0 1px 2px ${alpha(theme.palette.common.black, 0.18)}`,
          zIndex: 2,
        }}
      >
        <Stack spacing={1.5}>
          {/* Agent identity title block */}
          <Box>
            <Typography
              variant="overline"
              sx={{ display: 'block', letterSpacing: '0.12em', color: 'text.secondary', lineHeight: 1.4 }}
            >
              Configure your agent
            </Typography>
            <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <Typography
                variant="h6"
                sx={{ fontWeight: 600, lineHeight: 1.2, color: name.trim() ? 'text.primary' : 'text.secondary' }}
              >
                {name.trim() || 'New Agent'}
              </Typography>
              <Chip
                size="small"
                variant="outlined"
                label={
                  gettingStartedMode === 'clone'
                    ? `Custom — cloned from ${selectedCloneSource?.name || '(no source)'}`
                    : gettingStartedMode === 'scratch'
                      ? 'Custom — from scratch'
                      : `Template: ${selectedTemplate?.name || parentAgent?.agent_name || '(none selected)'}`
                }
                sx={{ maxWidth: 280, '& .MuiChip-label': { fontSize: '0.72rem' } }}
              />
            </Box>
          </Box>

          {/* Section navigation */}
          <StyledToggleButtonGroup
            exclusive
            size="small"
            value={workshopSection}
            onChange={(_event, value) => value && setWorkshopSection(value)}
            sx={{ width: '100%', '& .MuiToggleButton-root': { flex: 1 } }}
          >
            <ToggleButton value="setup">Setup</ToggleButton>
            <ToggleButton value="prompt">Prompt</ToggleButton>
            <ToggleButton value="tools">Tools</ToggleButton>
            <ToggleButton value="reference">Reference</ToggleButton>
          </StyledToggleButtonGroup>
        </Stack>
      </Box>

      {/* ── Scrollable content region ── */}
      <Box sx={{ flex: 1, p: 2.5, overflow: 'auto' }}>
        <Stack spacing={3}>
          {error && <Alert severity="error" sx={{ borderRadius: 2 }}>{error}</Alert>}
          {status && <Alert severity="success" sx={{ borderRadius: 2 }}>{status}</Alert>}

          {/* ── Section 1 (setup): Identity & Configuration ── */}
          {workshopSection === 'setup' && (
          <SectionCard elevation={0}>
            <SectionHeader>Identity & Configuration</SectionHeader>

            <Stack spacing={2}>
              {/* Getting Started mode selector */}
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ mb: 0.75, display: 'block' }}>
                  Starting point
                </Typography>
                <StyledToggleButtonGroup
                  exclusive
                  size="small"
                  value={gettingStartedMode}
                  onChange={(_event, value) => {
                    if (value !== null) {
                      startDraft(value as GettingStartedMode)
                    }
                  }}
                >
                  <ToggleButton value="template">Template</ToggleButton>
                  <ToggleButton value="scratch">Scratch</ToggleButton>
                  <ToggleButton value="clone">Clone</ToggleButton>
                </StyledToggleButtonGroup>
              </Box>

              {gettingStartedMode === 'template' && (
                <Stack spacing={1} sx={{ maxWidth: 520 }}>
                  <FormControl size="small" sx={{ maxWidth: 360 }}>
                    <InputLabel>Template</InputLabel>
                    <Select
                      label="Template"
                      value={parentAgentId}
                      disabled={templateOptions.length === 0}
                      onChange={(event) => setParentAgentId(event.target.value)}
                    >
                      {templateOptions.length === 0 ? (
                        <MenuItem value="" disabled>
                          No templates available
                        </MenuItem>
                      ) : (
                        templateOptions.map((template) => (
                          <MenuItem key={template.agent_id} value={template.agent_id}>
                            {template.name}
                          </MenuItem>
                        ))
                      )}
                    </Select>
                  </FormControl>
                  {selectedTemplate && selectedTemplate.allowed_group_ids.length > 0 && (
                    <Alert severity="info" icon={<LockOutlinedIcon fontSize="inherit" />}>
                      Package restriction (read-only): available to {selectedTemplate.allowed_group_ids.join(', ')}.
                      A custom copy may keep or narrow this restriction, but cannot widen it.
                    </Alert>
                  )}
                </Stack>
              )}

              {gettingStartedMode === 'clone' && (
                <FormControl size="small" sx={{ maxWidth: 360 }}>
                  <InputLabel>Clone Source</InputLabel>
                  <Select
                    label="Clone Source"
                    value={cloneSourceAgentId}
                    onChange={(event) => setCloneSourceAgentId(event.target.value)}
                  >
                    {customAgents.map((agent) => (
                      <MenuItem key={agent.id} value={agent.id}>
                        {agent.name}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}

              {/* Icon + Agent Name row */}
              <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start', maxWidth: 420 }}>
                <FormControl size="small" sx={{ width: 72, flexShrink: 0 }}>
                  <InputLabel>Icon</InputLabel>
                  <Select
                    label="Icon"
                    value={icon}
                    onChange={(event) => setIcon(event.target.value)}
                    sx={{ '& .MuiSelect-select': { textAlign: 'center', fontSize: '1.1rem' } }}
                  >
                    {iconOptions.map((option) => (
                      <MenuItem key={option} value={option}>
                        {option}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <TextField
                  size="small"
                  label="Agent Name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  sx={{ flex: 1 }}
                />
              </Box>

              <TextField
                fullWidth
                multiline
                minRows={2}
                maxRows={5}
                size="small"
                label="Description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Brief description of what this agent does"
              />

              {domainEnvelopeMetadata && (
                <>
                  <DomainEnvelopeMetadataPanel
                    metadata={domainEnvelopeMetadata}
                    compact
                    title="Envelope & Validation"
                    validationModeNote="This template produces domain-envelope objects. Automatic validation defaults come from the domain pack; custom validation can be added in Flow Builder with validation attachments and steering prompts."
                  />
                  <Divider sx={{ opacity: 0.5 }} />
                </>
              )}

              {/* Model & behavior */}
              <Typography
                variant="overline"
                sx={{ display: 'block', letterSpacing: '0.1em', color: 'text.secondary', mt: 0.5 }}
              >
                Model & behavior
              </Typography>

              {/* Model & Visibility */}
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Typography variant="caption" color="text.secondary">
                  Model guidance
                </Typography>
                <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{buildModelHelpText(modelOptions)}</span>} placement="top">
                  <IconButton aria-label="Show configured model guidance" size="small" sx={{ p: 0.25 }}>
                    <HelpOutlineIcon sx={{ fontSize: 15 }} />
                  </IconButton>
                </Tooltip>
              </Stack>
              <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
                <FormControl size="small" sx={{ minWidth: 200, flex: 1 }}>
                  <InputLabel>Model</InputLabel>
                  <Select
                    label="Model"
                    value={selectedModelId}
                    onChange={(event) => handleModelChange(event.target.value)}
                  >
                    {modelOptions
                      // Backend already returns only curator-visible models (config-driven).
                      .map((model) => (
                        <MenuItem key={model.model_id} value={model.model_id}>
                          {model.name}
                        </MenuItem>
                      ))}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 180, flex: 1 }}>
                  <InputLabel>Visibility</InputLabel>
                  <Select
                    label="Visibility"
                    value={selectedVisibility}
                    onChange={(event) => setSelectedVisibility(event.target.value as 'private' | 'project')}
                  >
                    <MenuItem value="private">Private</MenuItem>
                    <MenuItem value="project">Shared with Project</MenuItem>
                  </Select>
                </FormControl>
              </Box>

              <FormControl size="small" fullWidth>
                <InputLabel id="available-groups-label">Available to groups</InputLabel>
                <Select
                  labelId="available-groups-label"
                  label="Available to groups"
                  multiple
                  value={selectedAllowedGroupIds}
                  aria-describedby="available-groups-helper-text"
                  onChange={(event) => {
                    const value = event.target.value as string[]
                    const nextValue = value.includes(ALL_GROUPS_VALUE) ? [] : value
                    if (inheritedAllowedGroupIds.length > 0 && nextValue.length === 0) return
                    setSelectedAllowedGroupIds(nextValue)
                  }}
                  renderValue={(selected) => {
                    const groupIds = selected as string[]
                    return groupIds.length === 0 ? 'All groups' : groupIds.join(', ')
                  }}
                >
                  {inheritedAllowedGroupIds.length === 0 && (
                    <MenuItem value={ALL_GROUPS_VALUE}>
                      <Checkbox checked={selectedAllowedGroupIds.length === 0} />
                      <ListItemText primary="All groups" />
                    </MenuItem>
                  )}
                  {selectableGroupOptions.map((group) => (
                    <MenuItem key={group.group_id} value={group.group_id}>
                      <Checkbox checked={selectedAllowedGroupIds.includes(group.group_id)} />
                      <ListItemText primary={group.name} secondary={group.group_id} />
                    </MenuItem>
                  ))}
                </Select>
                <FormHelperText id="available-groups-helper-text">
                  Sharing determines which people or projects could otherwise see this agent. Allowed groups further
                  restrict which authenticated curator groups may use it. Group-specific instructions only change
                  behavior after access is granted.
                  {inheritedAllowedGroupIds.length > 0
                    ? ` This copy inherits a ${inheritedAllowedGroupIds.join(', ')} access floor and may only be narrowed.`
                    : ''}
                </FormHelperText>
              </FormControl>

              {selectedModelOption && (
                <Box
                  sx={{
                    border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.18)}`,
                    borderRadius: 1.5,
                    p: 1.5,
                    backgroundColor: (theme) => alpha(theme.palette.background.default, 0.35),
                  }}
                >
                  <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={1.5}>
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {selectedModelOption.name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {selectedModelOption.provider.toUpperCase()} · {selectedModelOption.model_id}
                      </Typography>
                    </Box>
                    {onVerifyRequest && (
                      <Button size="small" variant="outlined" onClick={handleAskClaudeAboutModels}>
                        Confused about models? Chat with Claude
                      </Button>
                    )}
                  </Stack>

                  {selectedModelOption.description && (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                      {selectedModelOption.description}
                    </Typography>
                  )}

                  {selectedModelOption.guidance && selectedModelOption.guidance !== selectedModelOption.description && (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
                      {selectedModelOption.guidance}
                    </Typography>
                  )}

                  {(selectedModelOption.recommended_for.length > 0 || selectedModelOption.avoid_for.length > 0) && (
                    <Box sx={{ mt: 1.5 }}>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                        Model fit
                      </Typography>
                      {selectedModelOption.recommended_for.length > 0 && (
                        <>
                          <Typography variant="caption" color="text.secondary">
                            Recommended for
                          </Typography>
                          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', mt: 0.5 }}>
                            {selectedModelOption.recommended_for.map((item) => (
                              <Chip key={item} size="small" variant="outlined" label={item} />
                            ))}
                          </Box>
                        </>
                      )}
                      {selectedModelOption.avoid_for.length > 0 && (
                        <>
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.25 }}>
                            Avoid for
                          </Typography>
                          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', mt: 0.5 }}>
                            {selectedModelOption.avoid_for.map((item) => (
                              <Chip key={item} size="small" variant="outlined" label={item} />
                            ))}
                          </Box>
                        </>
                      )}
                    </Box>
                  )}

                  {selectedModelOption.supports_reasoning && selectedModelOption.reasoning_options.length > 0 && (
                    <Box sx={{ mt: 1.5 }}>
                      <Divider sx={{ mb: 1.25, opacity: 0.6 }} />
                      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 0.5 }}>
                        <Typography variant="caption" color="text.secondary">
                          Reasoning level
                        </Typography>
                        <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{REASONING_HELP_TEXT}</span>} placement="top">
                          <IconButton size="small" sx={{ p: 0.25 }}>
                            <HelpOutlineIcon sx={{ fontSize: 14 }} />
                          </IconButton>
                        </Tooltip>
                      </Stack>
                      <FormControl size="small" sx={{ minWidth: 220, maxWidth: 320 }}>
                        <InputLabel>Reasoning</InputLabel>
                        <Select
                          label="Reasoning"
                          value={selectedModelReasoning}
                          onChange={(event) => setSelectedModelReasoning(event.target.value)}
                        >
                          {selectedModelOption.reasoning_options.map((reasoningOption) => (
                            <MenuItem key={reasoningOption} value={reasoningOption}>
                              {formatReasoningLabel(reasoningOption)}
                            </MenuItem>
                          ))}
                        </Select>
                        {selectedModelReasoningDescription && (
                          <FormHelperText>{selectedModelReasoningDescription}</FormHelperText>
                        )}
                      </FormControl>
                    </Box>
                  )}
                </Box>
              )}

            </Stack>
          </SectionCard>
          )}

          {/* ── Section (prompt): Prompt ── */}
          {workshopSection === 'prompt' && (
          <SectionCard elevation={0}>
            <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1.5} sx={{ mb: 2 }}>
              <SectionHeader sx={{ mb: 0 }}>Prompt</SectionHeader>
              {onVerifyRequest && (
                <Button size="small" variant="outlined" onClick={handleDiscussPromptChangesWithClaude}>
                  Discuss prompt changes with Claude
                </Button>
              )}
            </Stack>
            <Stack spacing={1}>
              <StyledAccordion defaultExpanded={true}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Main / base prompt</Typography>
                </AccordionSummary>
                <AccordionDetails>
                  {overlayStatus === 'needs_review' && (
                    <Alert severity="warning" sx={{ mb: 1.5 }}>
                      {overlayWarning || 'This saved main prompt contains locked/core prompt markers that need coordinator review before the final prompt is trusted.'}
                    </Alert>
                  )}
                  <TextField
                    fullWidth
                    multiline
                    minRows={14}
                    value={customPrompt}
                    onChange={(event) => setCustomPrompt(event.target.value)}
                    placeholder="Edit the main prompt for this custom agent."
                    variant="outlined"
                    sx={{
                      '& .MuiInputBase-root': {
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                        fontSize: '0.85rem',
                        backgroundColor: (theme) => alpha(theme.palette.common.black, 0.15),
                        borderRadius: 1.5,
                      },
                      '& .MuiOutlinedInput-notchedOutline': {
                        borderColor: (theme) => alpha(theme.palette.divider, 0.3),
                      },
                    }}
                  />
                </AccordionDetails>
              </StyledAccordion>

              <StyledAccordion defaultExpanded={true}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Group-specific instructions</Typography>
                    {hasAnyGroupOverrides && (
                      <Chip size="small" label={`${Object.keys(groupPromptOverrides).length} override${Object.keys(groupPromptOverrides).length !== 1 ? 's' : ''}`} color="warning" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                    )}
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <Stack spacing={1.5}>
                    <Alert severity="info" sx={{ borderRadius: 1.5 }}>
                      Logged in as {loggedInAsLabel}. Group membership: {loggedInGroupsLabel}.
                      {selectedGroupId ? ` Editing ${selectedGroupId} instructions.` : ' Select a group to edit its instructions.'}
                    </Alert>
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <FormControlLabel
                        sx={{ ml: 0, mr: 0 }}
                        control={
                          <Switch
                            size="small"
                            checked={includeGroupRules}
                            onChange={(event) => setIncludeGroupRules(event.target.checked)}
                          />
                        }
                        label={
                          <Typography variant="body2" color="text.secondary">
                            Add group prompts at runtime
                          </Typography>
                        }
                      />
                      <Tooltip
                        title="When enabled, group-specific instructions are included at runtime for this agent."
                        placement="top"
                      >
                        <IconButton size="small" sx={{ p: 0.25 }} aria-label="group prompt runtime help">
                          <HelpOutlineIcon sx={{ fontSize: 14 }} />
                        </IconButton>
                      </Tooltip>
                    </Stack>

                    {availableGroupIds.length === 0 ? (
                      <Typography variant="body2" color="text.secondary">
                        This template has no group-specific prompts to override.
                      </Typography>
                    ) : (
                      <Stack spacing={1.5}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Select
                            size="small"
                            value={groupId}
                            displayEmpty
                            onChange={(event) => setGroupId(event.target.value)}
                            sx={{ minWidth: 180 }}
                          >
                            <MenuItem value="">
                              Select group
                            </MenuItem>
                            {availableGroupIds.map((availableGroupId) => (
                              <MenuItem key={availableGroupId} value={availableGroupId}>
                                {availableGroupId}
                              </MenuItem>
                            ))}
                          </Select>
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={handleResetSelectedGroupPrompt}
                            disabled={!hasSelectedGroupOverride}
                          >
                            Reset to Template
                          </Button>
                        </Stack>
                        <TextField
                          fullWidth
                          multiline
                          minRows={10}
                          label={selectedGroupId ? `${selectedGroupId} instructions` : 'Group-specific instructions'}
                          value={selectedGroupPrompt}
                          onChange={(event) => handleSelectedGroupPromptChange(event.target.value)}
                          disabled={!selectedGroupId}
                          sx={{
                            '& .MuiInputBase-root': {
                              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                              fontSize: '0.8rem',
                              backgroundColor: (theme) => alpha(theme.palette.common.black, 0.15),
                              borderRadius: 1.5,
                            },
                            '& .MuiOutlinedInput-notchedOutline': {
                              borderColor: (theme) => alpha(theme.palette.divider, 0.3),
                            },
                          }}
                        />
                        <Typography variant="caption" color="text.secondary">
                          {selectedGroupId
                            ? hasSelectedGroupOverride
                              ? `Custom override active for ${selectedGroupId}.`
                              : `Using template ${selectedGroupId} prompt content.`
                            : 'No group selected.'}
                          {hasAnyGroupOverrides ? ` Total overrides: ${Object.keys(groupPromptOverrides).length}.` : ''}
                        </Typography>
                      </Stack>
                    )}
                  </Stack>
                </AccordionDetails>
              </StyledAccordion>
            </Stack>
          </SectionCard>
          )}

          {/* ── Section (reference): Reference (read-only) ── */}
          {workshopSection === 'reference' && (
          <SectionCard elevation={0}>
            <SectionHeader>Reference (read-only)</SectionHeader>
            <Box
              sx={{
                mb: 2,
                p: 1.5,
                borderRadius: 1.5,
                backgroundColor: (theme) => alpha(theme.palette.info.main, 0.08),
              }}
            >
              <Typography variant="body2" color="text.secondary">
                These are the built-in instruction layers that make up this agent. They&apos;re read-only here — shown so you can see what your own instructions (on the Prompt tab) build on. You don&apos;t need to change anything on this tab.
              </Typography>
            </Box>
            <Stack spacing={1}>
              <StyledAccordion defaultExpanded={false}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Built-in instructions</Typography>
                    <Chip size="small" icon={<LockOutlinedIcon />} label="Locked" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <PromptLayerPreview>
                    {parentCorePrompt || 'No backend-owned core prompt layer was returned for this template.'}
                  </PromptLayerPreview>
                </AccordionDetails>
              </StyledAccordion>

              <StyledAccordion defaultExpanded={false}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Output structure</Typography>
                    <Chip size="small" icon={<LockOutlinedIcon />} label="Automatic" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <PromptLayerPreview>
                    {parentGeneratedContract || 'No generated runtime contract layer is required for this template.'}
                  </PromptLayerPreview>
                </AccordionDetails>
              </StyledAccordion>

              <StyledAccordion defaultExpanded={false}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Template instructions</Typography>
                    <Chip size="small" label="From template" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <PromptLayerPreview>
                    {parentBasePrompt || 'No base prompt is available for this template.'}
                  </PromptLayerPreview>
                </AccordionDetails>
              </StyledAccordion>

            </Stack>
          </SectionCard>
          )}

          {/* ── Section (tools): Advanced Settings ── */}
          {workshopSection === 'tools' && (
          <SectionCard elevation={0}>
            <SectionHeader>Advanced Settings</SectionHeader>

            {/* Tools accordion */}
            <StyledAccordion defaultExpanded={selectedToolIds.length > 0}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Tools</Typography>
                  {selectedToolIds.length > 0 && (
                    <Chip size="small" label={`${selectedToolIds.length} attached`} color="primary" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                  )}
                </Stack>
              </AccordionSummary>
              <AccordionDetails>
                <Stack spacing={1.5}>
                  {selectedToolIds.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      No tools selected.
                    </Typography>
                  ) : (
                    <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                      {selectedToolIds.map((toolId) => {
                        const tool = toolLibrary.find((entry) => entry.tool_key === toolId)
                        return (
                          <Chip
                            key={toolId}
                            size="small"
                            label={tool?.display_name || toolId}
                            onDelete={() => removeTool(toolId)}
                          />
                        )
                      })}
                    </Box>
                  )}
                  <Stack direction="row" spacing={1} flexWrap="wrap">
                    <Button size="small" variant="outlined" onClick={handleOpenToolLibrary}>
                      Manage Tools
                    </Button>
                    {onVerifyRequest && (
                      <Button size="small" variant="outlined" onClick={handleAskClaudeForTool}>
                        Need a new tool? Ask Claude
                      </Button>
                    )}
                    <Button size="small" variant="contained" onClick={handleOpenToolIdeaDialog}>
                      Send to Developers
                    </Button>
                  </Stack>
                </Stack>
              </AccordionDetails>
            </StyledAccordion>

            {/* Tool Requests accordion */}
            <StyledAccordion>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Tool Requests</Typography>
                  {toolIdeaRequests.length > 0 && (
                    <Chip size="small" label={toolIdeaRequests.length} variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                  )}
                  {toolIdeasLoading && <CircularProgress size={14} />}
                </Stack>
              </AccordionSummary>
              <AccordionDetails>
                {toolIdeaRequests.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No tool requests submitted yet.
                  </Typography>
                ) : (
                  <Stack spacing={1}>
                    {toolIdeaRequests.slice(0, 8).map((request) => (
                      <Stack
                        key={request.id}
                        direction="row"
                        justifyContent="space-between"
                        alignItems="center"
                        spacing={1}
                      >
                        <Box sx={{ minWidth: 0 }}>
                          <Typography variant="body2" noWrap>
                            {request.title}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {new Date(request.created_at).toLocaleDateString()}
                          </Typography>
                        </Box>
                        <Chip
                          size="small"
                          color={toolIdeaStatusColor(request.status)}
                          label={toolIdeaStatusLabel(request.status)}
                        />
                      </Stack>
                    ))}
                  </Stack>
                )}
              </AccordionDetails>
            </StyledAccordion>
          </SectionCard>
          )}

          {/* ── Section 4: Save & History ── */}
          <SectionCard elevation={0}>
            <SectionHeader>Save & History</SectionHeader>

            <Stack spacing={2}>
              <TextField
                fullWidth
                size="small"
                label="Save Notes"
                value={saveNotes}
                onChange={(event) => setSaveNotes(event.target.value)}
                placeholder="Optional notes for version history (saved via File > Save)"
              />

              <StyledAccordion defaultExpanded={versions.length > 0 && versions.length <= 5}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Version History</Typography>
                    {versions.length > 0 && (
                      <Chip size="small" label={`${versions.length} version${versions.length !== 1 ? 's' : ''}`} variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                    )}
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  {versions.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      No versions yet
                    </Typography>
                  ) : (
                    <Stack spacing={0.75}>
                      {versions.map((version) => (
                        <Stack
                          key={version.id}
                          direction="row"
                          spacing={1}
                          alignItems="center"
                          justifyContent="space-between"
                          sx={{
                            py: 0.5,
                            px: 1,
                            borderRadius: 1,
                            '&:hover': {
                              backgroundColor: (theme) => alpha(theme.palette.action.hover, 0.5),
                            },
                          }}
                        >
                          <Typography variant="body2">
                            v{version.version} {version.notes ? `- ${version.notes}` : ''}
                          </Typography>
                          <Button
                            size="small"
                            variant="text"
                            onClick={() => void handleRevertWithNotes(version.version)}
                            disabled={!selectedCustomAgentId || saving}
                          >
                            Revert
                          </Button>
                        </Stack>
                      ))}
                    </Stack>
                  )}
                </AccordionDetails>
              </StyledAccordion>
            </Stack>
          </SectionCard>

        <Dialog
          open={openDialogOpen}
          onClose={handleOpenDialogClose}
          maxWidth="sm"
          fullWidth
          PaperProps={{ sx: { borderRadius: 2, maxHeight: '70vh' } }}
        >
          <DialogTitle sx={{ pb: 1 }}>
            <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Open Agent
            </Typography>
          </DialogTitle>
          <DialogContent sx={{ pt: 1 }}>
            <TextField
              fullWidth
              size="small"
              placeholder="Search agents..."
              value={openSearchTerm}
              onChange={(event) => setOpenSearchTerm(event.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                  </InputAdornment>
                ),
              }}
              sx={{ mb: 2 }}
            />
            <Box sx={{ minHeight: 200, maxHeight: 320, overflow: 'auto' }}>
              {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : filteredOpenAgents.length === 0 ? (
                <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                  <Typography variant="body2">
                    {openSearchTerm ? 'No agents match your search' : 'No saved agents yet'}
                  </Typography>
                </Box>
              ) : (
                <List disablePadding>
                  {filteredOpenAgents.map((agent) => (
                    <ListItem key={agent.id} disablePadding>
                      <ListItemButton
                        onClick={() => {
                          selectCustomAgent(agent.id)
                          handleOpenDialogClose()
                        }}
                        selected={agent.id === selectedCustomAgentId}
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
                            primary={agent.name}
                            secondary={agent.description || 'Custom agent'}
                            primaryTypographyProps={{ fontSize: '0.85rem' }}
                            secondaryTypographyProps={{ fontSize: '0.75rem' }}
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

        <Dialog
          open={manageDialogOpen}
          onClose={handleManageDialogClose}
          maxWidth="sm"
          fullWidth
          PaperProps={{ sx: { borderRadius: 2, maxHeight: '70vh' } }}
        >
          <DialogTitle sx={{ pb: 1 }}>
            <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Manage Agents
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Open or delete your saved agents
            </Typography>
          </DialogTitle>
          <DialogContent sx={{ pt: 1 }}>
            <Box sx={{ minHeight: 200, maxHeight: 360, overflow: 'auto' }}>
              {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : customAgents.length === 0 ? (
                <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                  <Typography variant="body2">No saved agents yet</Typography>
                </Box>
              ) : (
                <List disablePadding>
                  {customAgents.map((agent) => (
                    <ListItem
                      key={agent.id}
                      disablePadding
                      sx={{
                        mb: 0.5,
                        border: (theme) => `1px solid ${theme.palette.divider}`,
                        borderRadius: 1,
                        backgroundColor:
                          agent.id === selectedCustomAgentId
                            ? (theme) => alpha(theme.palette.primary.main, 0.08)
                            : 'transparent',
                      }}
                    >
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
                            {agent.name}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {agent.description || 'Custom agent'}
                            {agent.id === selectedCustomAgentId && ' • Currently open'}
                          </Typography>
                        </Box>
                        <Button
                          size="small"
                          variant="text"
                          onClick={() => {
                            selectCustomAgent(agent.id)
                          }}
                        >
                          Open
                        </Button>
                        <Tooltip title="Delete">
                          <IconButton
                            size="small"
                            onClick={() => requestDelete(agent)}
                            sx={{ color: 'error.main' }}
                            disabled={saving}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
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

        <Dialog
          open={saveAsDialogOpen}
          onClose={handleSaveAsClose}
          maxWidth="xs"
          fullWidth
          PaperProps={{ sx: { borderRadius: 2 } }}
        >
          <DialogTitle sx={{ pb: 1 }}>
            <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Save Agent As
            </Typography>
          </DialogTitle>
          <DialogContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Enter a name for the new copy
            </Typography>
            <TextField
              fullWidth
              size="small"
              label="Agent Name"
              value={saveAsName}
              onChange={(event) => setSaveAsName(event.target.value)}
              autoFocus
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  void handleSaveAsConfirm()
                }
              }}
            />
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={handleSaveAsClose} size="small" disabled={saving}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleSaveAsConfirm()}
              variant="contained"
              size="small"
              disabled={saving || !saveAsName.trim()}
            >
              {saving ? 'Saving...' : 'Save As'}
            </Button>
          </DialogActions>
        </Dialog>

        <Dialog
          open={toolLibraryDialogOpen}
          onClose={handleCloseToolLibrary}
          maxWidth="sm"
          fullWidth
          PaperProps={{ sx: { borderRadius: 2, maxHeight: '75vh' } }}
        >
          <DialogTitle sx={{ pb: 1 }}>
            <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Tool Library
            </Typography>
          </DialogTitle>
          <DialogContent sx={{ pt: 1 }}>
            <TextField
              fullWidth
              size="small"
              placeholder="Search tools..."
              value={toolLibrarySearch}
              onChange={(event) => setToolLibrarySearch(event.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                  </InputAdornment>
                ),
              }}
              sx={{ mb: 2 }}
            />
            <FormControl size="small" fullWidth sx={{ mb: 2 }}>
              <InputLabel>Category</InputLabel>
              <Select
                label="Category"
                value={toolLibraryCategory}
                onChange={(event) => setToolLibraryCategory(event.target.value)}
              >
                <MenuItem value="all">All categories</MenuItem>
                {toolCategories.map((category) => (
                  <MenuItem key={category} value={category}>
                    {category}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Box sx={{ minHeight: 240, maxHeight: 380, overflow: 'auto' }}>
              {filteredToolLibrary.length === 0 ? (
                <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                  <Typography variant="body2">No tools match your search</Typography>
                </Box>
              ) : (
                <List disablePadding>
                  {filteredToolLibrary.map((tool) => {
                    const selected = selectedToolIds.includes(tool.tool_key)
                    const attachable = tool.allow_attach
                    return (
                      <ListItem key={tool.tool_key} disablePadding>
                        <ListItemButton
                          onClick={() => {
                            if (!attachable) return
                            toggleTool(tool.tool_key)
                          }}
                          selected={selected}
                          disabled={!attachable}
                          sx={{
                            borderRadius: 1,
                            mb: 0.5,
                            alignItems: 'flex-start',
                            opacity: attachable ? 1 : 0.55,
                            '&.Mui-selected': {
                              backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.12),
                            },
                          }}
                        >
                          <Checkbox
                            size="small"
                            edge="start"
                            checked={selected}
                            tabIndex={-1}
                            disableRipple
                            disabled={!attachable}
                          />
                          <ListItemText
                            primary={tool.display_name}
                            secondary={
                              attachable
                                ? `${tool.category} • ${tool.description}`
                                : `${tool.category} • ${tool.description} • Not attachable by policy`
                            }
                            primaryTypographyProps={{ fontSize: '0.85rem' }}
                            secondaryTypographyProps={{ fontSize: '0.75rem' }}
                          />
                        </ListItemButton>
                      </ListItem>
                    )
                  })}
                </List>
              )}
            </Box>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={handleCloseToolLibrary} size="small">
              Done
            </Button>
          </DialogActions>
        </Dialog>

        <Dialog
          open={toolIdeaDialogOpen}
          onClose={handleCloseToolIdeaDialog}
          maxWidth="sm"
          fullWidth
          PaperProps={{ sx: { borderRadius: 2 } }}
        >
          <DialogTitle sx={{ pb: 1 }}>
            <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Submit Tool Request
            </Typography>
          </DialogTitle>
          <DialogContent sx={{ pt: 1 }}>
            <Stack spacing={1.5}>
              <Typography variant="body2" color="text.secondary">
                Share a concise request for the developers. You can draft it with Claude first.
              </Typography>
              <TextField
                size="small"
                fullWidth
                label="Title"
                value={toolIdeaTitle}
                onChange={(event) => setToolIdeaTitle(event.target.value)}
                placeholder="Example: Add GO synonym expansion tool"
              />
              <TextField
                fullWidth
                multiline
                minRows={6}
                label="Description"
                value={toolIdeaDescription}
                onChange={(event) => setToolIdeaDescription(event.target.value)}
                placeholder="Describe the problem, required inputs, expected output, and one example use case."
              />
            </Stack>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={handleCloseToolIdeaDialog} size="small" disabled={toolIdeaSubmitting}>
              Cancel
            </Button>
            <Button
              onClick={handleSubmitToolIdea}
              variant="contained"
              size="small"
              disabled={toolIdeaSubmitting}
            >
              {toolIdeaSubmitting ? 'Submitting...' : 'Submit'}
            </Button>
          </DialogActions>
        </Dialog>

        <Dialog
          open={selfExclusionDialogOpen}
          onClose={cancelSelfExclusion}
          maxWidth="xs"
          fullWidth
        >
          <DialogTitle>Save a restriction that excludes you?</DialogTitle>
          <DialogContent>
            <Alert severity="warning">
              Available to groups is set to {selectedAllowedGroupIds.join(', ')}, but your current groups are
              {' '}{currentUserGroupIds.join(', ')}. After saving, server authorization may prevent you from using
              this agent.
            </Alert>
          </DialogContent>
          <DialogActions>
            <Button onClick={cancelSelfExclusion}>Go back</Button>
            <Button
              variant="contained"
              color="warning"
              onClick={confirmSelfExclusion}
            >
              Save restriction
            </Button>
          </DialogActions>
        </Dialog>

        <Dialog
          open={deleteConfirmOpen}
          onClose={handleDeleteCancel}
          PaperProps={{ sx: { minWidth: 320, borderRadius: 2 } }}
        >
          <DialogTitle sx={{ fontSize: '1rem' }}>Delete Agent?</DialogTitle>
          <DialogContent>
            <Typography variant="body2" color="text.secondary">
              Are you sure you want to delete &ldquo;{pendingDeleteAgent?.name}&rdquo;? This action cannot be undone.
            </Typography>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={handleDeleteCancel} disabled={saving} size="small">
              Cancel
            </Button>
            <Button
              onClick={handleDeleteConfirm}
              color="error"
              variant="contained"
              disabled={saving}
              size="small"
            >
              {saving ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogActions>
        </Dialog>
        </Stack>
      </Box>
    </Box>
  )
}

export default PromptWorkshop
