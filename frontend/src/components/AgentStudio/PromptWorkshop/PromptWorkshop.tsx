import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

import type {
  PromptCatalog,
  PromptInfo,
  CustomAgent,
  CustomAgentVersion,
  AgentWorkshopContext,
  ModelOption,
  ToolLibraryItem,
  AgentTemplate,
  ToolIdeaRequest,
  ToolIdeaConversationEntry,
  WorkshopPromptUpdateRequest,
} from '@/types/promptExplorer'
import {
  createCustomAgent,
  deleteCustomAgent,
  fetchAgentTemplates,
  fetchModelOptions,
  fetchToolLibrary,
  listToolIdeaRequests,
  listCustomAgentVersions,
  listCustomAgents,
  revertCustomAgentVersion,
  setCustomAgentVisibility,
  submitToolIdeaRequest,
  updateCustomAgent,
} from '@/services/agentStudioService'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'

const FALLBACK_ICON_OPTIONS = ['🔧', '🧬', '📄', '🔍', '🧪', '📊', '🧠', '⚙️', '✨', '📝', '📚', '🧩']

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
  border: `1px solid ${alpha(theme.palette.divider, 0.6)}`,
  backgroundColor: alpha(theme.palette.background.paper, 0.6),
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
  border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
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

function normalizeReasoningValue(value?: string | null): string {
  return (value || '').trim().toLowerCase()
}

function formatReasoningLabel(value: string): string {
  const normalized = normalizeReasoningValue(value)
  if (!normalized) return value
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function resolveModelSelection(
  modelOptions: ModelOption[],
  fallbackModelId: string,
  candidateModelId?: string | null
): string {
  const candidate = (candidateModelId || '').trim()
  if (candidate && modelOptions.some((model) => model.model_id === candidate)) {
    return candidate
  }
  return fallbackModelId
}

function resolveReasoningSelection(
  modelOptions: ModelOption[],
  modelId: string,
  candidateReasoning?: string | null
): string {
  const model = modelOptions.find((entry) => entry.model_id === modelId)
  if (!model || !model.supports_reasoning || model.reasoning_options.length === 0) {
    return ''
  }

  const normalizedCandidate = normalizeReasoningValue(candidateReasoning)
  if (normalizedCandidate && model.reasoning_options.includes(normalizedCandidate)) {
    return normalizedCandidate
  }

  const defaultReasoning = normalizeReasoningValue(model.default_reasoning)
  if (defaultReasoning && model.reasoning_options.includes(defaultReasoning)) {
    return defaultReasoning
  }

  return model.reasoning_options[0] || ''
}

const MODEL_HELP_TEXT = [
  'Quick guide:',
  '• openai/gpt-oss-120b (Groq): fastest for database lookups and validation loops.',
  '• gpt-5.4 (medium reasoning): default for complex PDF extraction and hard reasoning.',
  '• gpt-5-mini: balanced speed and quality for iterative drafting.',
].join('\n')

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

type GettingStartedMode = 'template' | 'scratch' | 'clone'

function PromptWorkshop({
  catalog,
  initialParentAgentId,
  initialCustomAgentId,
  onContextChange,
  onVerifyRequest,
  opusConversation = [],
  incomingPromptUpdate = null,
}: PromptWorkshopProps) {
  const { agents: agentMetadata, refresh: refreshAgentMetadata } = useAgentMetadata()

  const [parentAgentId, setParentAgentId] = useState('')
  const [gettingStartedMode, setGettingStartedMode] = useState<GettingStartedMode>('template')
  const [customAgents, setCustomAgents] = useState<CustomAgent[]>([])
  const [selectedCustomAgentId, setSelectedCustomAgentId] = useState<string>('')
  const [cloneSourceAgentId, setCloneSourceAgentId] = useState<string>('')
  const [versions, setVersions] = useState<CustomAgentVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')
  const [debouncedPromptDraft, setDebouncedPromptDraft] = useState('')
  const [modPromptOverrides, setModPromptOverrides] = useState<Record<string, string>>({})
  const [debouncedModPromptOverrides, setDebouncedModPromptOverrides] = useState<Record<string, string>>({})
  const [includeModRules, setIncludeModRules] = useState(true)
  const [selectedVisibility, setSelectedVisibility] = useState<'private' | 'project'>('private')
  const [selectedModelId, setSelectedModelId] = useState('')
  const [selectedModelReasoning, setSelectedModelReasoning] = useState('')
  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([])
  const [outputSchemaKey, setOutputSchemaKey] = useState('')
  const [icon, setIcon] = useState('🔧')
  const [saveNotes, setSaveNotes] = useState('')
  const [modId, setModId] = useState('')

  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [toolLibrary, setToolLibrary] = useState<ToolLibraryItem[]>([])
  const [templateOptions, setTemplateOptions] = useState<AgentTemplate[]>([])
  const [toolIdeaRequests, setToolIdeaRequests] = useState<ToolIdeaRequest[]>([])
  const [toolIdeasLoading, setToolIdeasLoading] = useState(false)
  const [toolIdeaDialogOpen, setToolIdeaDialogOpen] = useState(false)
  const [toolIdeaSubmitting, setToolIdeaSubmitting] = useState(false)
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
  const appliedInitialCustomAgentId = useRef<string | null>(null)
  const refreshAttemptedForInitialCustomAgentId = useRef<string | null>(null)
  const appliedPromptUpdateId = useRef<number | null>(null)

  const parentAgents = useMemo(() => {
    const seen = new Set<string>()
    const agents: PromptInfo[] = []
    for (const category of catalog.categories) {
      for (const agent of category.agents) {
        if (agent.agent_id === 'task_input') continue
        if (agent.agent_id.startsWith('ca_')) continue
        if (seen.has(agent.agent_id)) continue
        seen.add(agent.agent_id)
        agents.push(agent)
      }
    }
    return agents.sort((a, b) => a.agent_name.localeCompare(b.agent_name))
  }, [catalog])

  const parentAgent = useMemo(
    () => parentAgents.find((agent) => agent.agent_id === parentAgentId),
    [parentAgents, parentAgentId]
  )
  const selectedTemplate = useMemo(
    () => templateOptions.find((template) => template.agent_id === parentAgentId),
    [templateOptions, parentAgentId]
  )

  const selectedCustomAgent = useMemo(
    () => customAgents.find((agent) => agent.id === selectedCustomAgentId),
    [customAgents, selectedCustomAgentId]
  )
  const selectedCloneSource = useMemo(
    () => customAgents.find((agent) => agent.id === cloneSourceAgentId),
    [customAgents, cloneSourceAgentId]
  )

  const modRuleSourceAgent = useMemo(() => {
    const templateId = selectedCustomAgent?.template_source
      || selectedCloneSource?.template_source
      || (gettingStartedMode === 'template' ? parentAgentId : undefined)
    if (!templateId) return null
    return parentAgents.find((agent) => agent.agent_id === templateId) || null
  }, [
    gettingStartedMode,
    parentAgentId,
    parentAgents,
    selectedCloneSource?.template_source,
    selectedCustomAgent?.template_source,
  ])

  const availableModIds = useMemo(
    () => Object.keys(modRuleSourceAgent?.mod_rules || {}).sort(),
    [modRuleSourceAgent]
  )

  const selectedModId = useMemo(() => modId.trim().toUpperCase(), [modId])

  const selectedModBasePrompt = useMemo(() => {
    if (!selectedModId) return ''
    return modRuleSourceAgent?.mod_rules[selectedModId]?.content || ''
  }, [modRuleSourceAgent, selectedModId])

  const selectedModPrompt = useMemo(() => {
    if (!selectedModId) return ''
    if (Object.prototype.hasOwnProperty.call(modPromptOverrides, selectedModId)) {
      return modPromptOverrides[selectedModId]
    }
    return selectedModBasePrompt
  }, [modPromptOverrides, selectedModId, selectedModBasePrompt])

  const selectedModPromptForContext = useMemo(() => {
    if (!selectedModId) return undefined
    if (Object.prototype.hasOwnProperty.call(debouncedModPromptOverrides, selectedModId)) {
      return debouncedModPromptOverrides[selectedModId]
    }
    return modRuleSourceAgent?.mod_rules[selectedModId]?.content
  }, [debouncedModPromptOverrides, modRuleSourceAgent, selectedModId])

  const hasSelectedModOverride = useMemo(
    () => Boolean(selectedModId && Object.prototype.hasOwnProperty.call(modPromptOverrides, selectedModId)),
    [modPromptOverrides, selectedModId]
  )

  const hasAnyModOverrides = useMemo(
    () => Object.keys(modPromptOverrides).length > 0,
    [modPromptOverrides]
  )

  const iconOptions = useMemo(() => {
    const discovered = Object.values(agentMetadata)
      .map((agent) => agent.icon)
      .filter((candidate): candidate is string => Boolean(candidate && candidate.trim()))
    return Array.from(new Set([...FALLBACK_ICON_OPTIONS, ...discovered, icon || '🔧']))
  }, [agentMetadata, icon])

  const filteredOpenAgents = useMemo(() => {
    if (!openSearchTerm.trim()) return customAgents
    const query = openSearchTerm.toLowerCase()
    return customAgents.filter((agent) => {
      return agent.name.toLowerCase().includes(query) || (agent.description || '').toLowerCase().includes(query)
    })
  }, [customAgents, openSearchTerm])

  const defaultModelId = useMemo(() => {
    const explicitDefault = modelOptions.find((model) => model.default)
    if (explicitDefault) return explicitDefault.model_id
    if (modelOptions.length > 0) return modelOptions[0].model_id
    return ''
  }, [modelOptions])

  const selectedModelOption = useMemo(
    () => modelOptions.find((model) => model.model_id === selectedModelId) || null,
    [modelOptions, selectedModelId]
  )

  const selectedModelReasoningDescription = useMemo(() => {
    if (!selectedModelOption || !selectedModelReasoning) return ''
    return selectedModelOption.reasoning_descriptions[selectedModelReasoning] || ''
  }, [selectedModelOption, selectedModelReasoning])

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

  const toolPolicyByKey = useMemo(
    () => new Map(toolLibrary.map((tool) => [tool.tool_key, tool])),
    [toolLibrary]
  )

  useEffect(() => {
    if (!initialParentAgentId) return
    if (templateOptions.some((template) => template.agent_id === initialParentAgentId)) {
      setParentAgentId(initialParentAgentId)
    }
  }, [initialParentAgentId, templateOptions])

  useEffect(() => {
    const targetId = (initialCustomAgentId || '').trim()
    if (!targetId) return
    if (appliedInitialCustomAgentId.current === targetId) return

    const found = customAgents.find((agent) => agent.id === targetId)
    if (!found) return

    appliedInitialCustomAgentId.current = targetId
    setSelectedCustomAgentId(found.id)
    setCloneSourceAgentId(found.id)
    setStatus(`Opened "${found.name}"`)
  }, [customAgents, initialCustomAgentId])

  useEffect(() => {
    async function loadWorkshopOptions() {
      try {
        const [models, tools, templates] = await Promise.all([
          fetchModelOptions(),
          fetchToolLibrary(),
          fetchAgentTemplates(),
        ])
        setModelOptions(models)
        setToolLibrary(tools)
        setTemplateOptions(templates)
        if (models.length === 0) {
          setError('No model options are configured. Add entries in config/models.yaml before creating agents.')
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load workshop options')
      }
    }
    void loadWorkshopOptions()
  }, [])

  useEffect(() => {
    async function loadToolIdeaRequests() {
      setToolIdeasLoading(true)
      try {
        const response = await listToolIdeaRequests()
        setToolIdeaRequests(response.tool_ideas)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load tool idea requests')
      } finally {
        setToolIdeasLoading(false)
      }
    }
    void loadToolIdeaRequests()
  }, [])

  useEffect(() => {
    if (!parentAgentId && templateOptions.length > 0) {
      setParentAgentId(templateOptions[0].agent_id)
    }
  }, [parentAgentId, templateOptions])

  useEffect(() => {
    if (!selectedModelId) return
    const resolvedReasoning = resolveReasoningSelection(
      modelOptions,
      selectedModelId,
      selectedModelReasoning
    )
    if (resolvedReasoning !== selectedModelReasoning) {
      setSelectedModelReasoning(resolvedReasoning)
    }
  }, [modelOptions, selectedModelId, selectedModelReasoning])

  const getTemplateAlignedAgentId = useCallback((agents: CustomAgent[]): string => {
    if (!parentAgentId) return ''
    return agents.find((agent) => agent.template_source === parentAgentId)?.id || ''
  }, [parentAgentId])

  const loadCustomAgents = useCallback(async (options?: { silent?: boolean }) => {
    const silent = options?.silent ?? false
    if (!silent) {
      setLoading(true)
      setError(null)
      setStatus(null)
    }

    try {
      const response = await listCustomAgents()
      setCustomAgents(response.custom_agents)

      const templateAlignedAgentId = getTemplateAlignedAgentId(response.custom_agents)

      if (response.custom_agents.length > 0) {
        setSelectedCustomAgentId((prev) => {
          const existing = response.custom_agents.find((agent) => agent.id === prev)
          if (!existing) return ''
          if (parentAgentId && existing.template_source !== parentAgentId) {
            return ''
          }
          return prev
        })
        setCloneSourceAgentId((prev) => {
          const stillExists = response.custom_agents.some((agent) => agent.id === prev)
          if (stillExists) return prev
          return templateAlignedAgentId || response.custom_agents[0].id
        })
      } else {
        setSelectedCustomAgentId('')
        setCloneSourceAgentId('')
        setVersions([])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load custom agents')
    } finally {
      if (!silent) {
        setLoading(false)
      }
    }
  }, [getTemplateAlignedAgentId, parentAgentId])

  useEffect(() => {
    void loadCustomAgents()
  }, [loadCustomAgents])

  useEffect(() => {
    const targetId = (initialCustomAgentId || '').trim()
    if (!targetId) return
    if (customAgents.some((agent) => agent.id === targetId)) return
    if (refreshAttemptedForInitialCustomAgentId.current === targetId) return

    refreshAttemptedForInitialCustomAgentId.current = targetId
    // Clone-to-workshop can create a new agent after this component already loaded.
    // Refresh once so the initial id can be resolved without requiring a full page reload.
    void loadCustomAgents({ silent: true })
  }, [customAgents, initialCustomAgentId, loadCustomAgents])

  useEffect(() => {
    async function loadVersions() {
      if (!selectedCustomAgentId) {
        setVersions([])
        return
      }
      try {
        const loaded = await listCustomAgentVersions(selectedCustomAgentId)
        setVersions(loaded)
      } catch {
        setVersions([])
      }
    }
    void loadVersions()
  }, [selectedCustomAgentId])

  useEffect(() => {
    if (!selectedCustomAgent) {
      if (gettingStartedMode === 'clone' && selectedCloneSource) {
        const clonedName = selectedCloneSource.name.endsWith(' (Copy)')
          ? selectedCloneSource.name
          : `${selectedCloneSource.name} (Copy)`
        setName(clonedName)
        setDescription(selectedCloneSource.description || '')
        setCustomPrompt(selectedCloneSource.custom_prompt)
        setDebouncedPromptDraft(selectedCloneSource.custom_prompt)
        setModPromptOverrides(selectedCloneSource.mod_prompt_overrides || {})
        setDebouncedModPromptOverrides(selectedCloneSource.mod_prompt_overrides || {})
        setIncludeModRules(selectedCloneSource.include_mod_rules)
        setSelectedVisibility('private')
        const cloneModelId = resolveModelSelection(modelOptions, defaultModelId, selectedCloneSource.model_id)
        setSelectedModelId(cloneModelId)
        setSelectedModelReasoning(
          resolveReasoningSelection(modelOptions, cloneModelId, selectedCloneSource.model_reasoning)
        )
        setSelectedToolIds(selectedCloneSource.tool_ids || [])
        setOutputSchemaKey(selectedCloneSource.output_schema_key || '')
        setIcon(selectedCloneSource.icon || '🔧')
        if (selectedCloneSource.template_source) {
          setParentAgentId(selectedCloneSource.template_source)
        }
        return
      }

      if (gettingStartedMode === 'scratch') {
        setName('')
        setDescription('')
        setCustomPrompt('')
        setDebouncedPromptDraft('')
        setModPromptOverrides({})
        setDebouncedModPromptOverrides({})
        setIncludeModRules(false)
        setSelectedVisibility('private')
        setSelectedModelId(defaultModelId)
        setSelectedModelReasoning(resolveReasoningSelection(modelOptions, defaultModelId))
        setSelectedToolIds([])
        setOutputSchemaKey('')
        setIcon('🔧')
        return
      }

      const basePrompt = parentAgent?.base_prompt || ''
      setName(parentAgent ? `${parentAgent.agent_name} (Custom)` : '')
      setDescription('')
      setCustomPrompt(basePrompt)
      setDebouncedPromptDraft(basePrompt)
      setModPromptOverrides({})
      setDebouncedModPromptOverrides({})
      setIncludeModRules(true)
      setSelectedVisibility('private')
      const templateModelId = resolveModelSelection(modelOptions, defaultModelId, selectedTemplate?.model_id)
      setSelectedModelId(templateModelId)
      setSelectedModelReasoning(resolveReasoningSelection(modelOptions, templateModelId))
      setSelectedToolIds(selectedTemplate?.tool_ids || [])
      setOutputSchemaKey(selectedTemplate?.output_schema_key || '')
      setIcon('🔧')
      return
    }

    setName(selectedCustomAgent.name)
    setDescription(selectedCustomAgent.description || '')
    setCustomPrompt(selectedCustomAgent.custom_prompt)
    setDebouncedPromptDraft(selectedCustomAgent.custom_prompt)
    setModPromptOverrides(selectedCustomAgent.mod_prompt_overrides || {})
    setDebouncedModPromptOverrides(selectedCustomAgent.mod_prompt_overrides || {})
    setIncludeModRules(selectedCustomAgent.include_mod_rules)
    setSelectedVisibility(selectedCustomAgent.visibility === 'project' ? 'project' : 'private')
    const customModelId = resolveModelSelection(modelOptions, defaultModelId, selectedCustomAgent.model_id)
    setSelectedModelId(customModelId)
    setSelectedModelReasoning(
      resolveReasoningSelection(modelOptions, customModelId, selectedCustomAgent.model_reasoning)
    )
    setSelectedToolIds(selectedCustomAgent.tool_ids || [])
    setOutputSchemaKey(selectedCustomAgent.output_schema_key || '')
    setIcon(selectedCustomAgent.icon || '🔧')
    if (selectedCustomAgent.template_source) {
      setParentAgentId(selectedCustomAgent.template_source)
    }
  }, [
    modelOptions,
    defaultModelId,
    gettingStartedMode,
    parentAgent,
    selectedCloneSource,
    selectedCustomAgent,
    selectedTemplate,
  ])

  useEffect(() => {
    if (availableModIds.length === 0) {
      if (modId) setModId('')
      return
    }
    if (!modId || !availableModIds.includes(modId)) {
      setModId(availableModIds[0])
    }
  }, [availableModIds, modId])

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedPromptDraft(customPrompt)
    }, 450)

    return () => {
      window.clearTimeout(timeout)
    }
  }, [customPrompt])

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedModPromptOverrides(modPromptOverrides)
    }, 450)

    return () => {
      window.clearTimeout(timeout)
    }
  }, [modPromptOverrides])

  useEffect(() => {
    if (!onContextChange) return
    const contextTemplateId = selectedCustomAgent?.template_source
      || (gettingStartedMode === 'template' ? parentAgentId : undefined)
    const contextTemplateName = contextTemplateId
      ? (selectedTemplate?.name || parentAgent?.agent_name)
      : undefined
    onContextChange({
      template_source: contextTemplateId || undefined,
      template_name: contextTemplateName,
      custom_agent_id: selectedCustomAgent?.agent_id,
      custom_agent_name: selectedCustomAgent?.name,
      include_mod_rules: includeModRules,
      selected_mod_id: selectedModId || undefined,
      prompt_draft: debouncedPromptDraft,
      selected_mod_prompt_draft: selectedModPromptForContext,
      mod_prompt_override_count: Object.keys(debouncedModPromptOverrides).length,
      has_mod_prompt_overrides: Object.keys(debouncedModPromptOverrides).length > 0,
      template_prompt_stale: selectedCustomAgent?.parent_prompt_stale,
      template_exists: selectedCustomAgent?.parent_exists,
      draft_tool_ids: selectedToolIds,
      draft_model_id: selectedModelId || undefined,
      draft_model_reasoning: selectedModelReasoning || undefined,
    })
  }, [
    gettingStartedMode,
    onContextChange,
    parentAgentId,
    parentAgent?.agent_name,
    selectedTemplate?.name,
    selectedCustomAgent?.agent_id,
    selectedCustomAgent?.name,
    selectedCustomAgent?.template_source,
    selectedCustomAgent?.parent_prompt_stale,
    selectedCustomAgent?.parent_exists,
    includeModRules,
    selectedModId,
    debouncedPromptDraft,
    selectedModPromptForContext,
    debouncedModPromptOverrides,
    selectedToolIds,
    selectedModelId,
    selectedModelReasoning,
  ])

  useEffect(() => {
    if (!incomingPromptUpdate) return
    if (appliedPromptUpdateId.current === incomingPromptUpdate.request_id) return
    appliedPromptUpdateId.current = incomingPromptUpdate.request_id

    if (
      incomingPromptUpdate.apply_mode
      && incomingPromptUpdate.apply_mode !== 'replace'
      && incomingPromptUpdate.apply_mode !== 'targeted_edit'
    ) {
      setError(`Unsupported prompt update mode: ${incomingPromptUpdate.apply_mode}`)
      return
    }
    if (typeof incomingPromptUpdate.prompt !== 'string' || !incomingPromptUpdate.prompt.trim()) {
      setError('Received an invalid prompt update payload')
      return
    }

    const targetPrompt = incomingPromptUpdate.target_prompt === 'mod' ? 'mod' : 'main'
    if (targetPrompt === 'mod') {
      const targetModId = (incomingPromptUpdate.target_mod_id || selectedModId || '').trim().toUpperCase()
      if (!targetModId) {
        setError('Cannot apply MOD prompt update because no MOD is selected.')
        return
      }
      if (availableModIds.length > 0 && !availableModIds.includes(targetModId)) {
        setError(`Cannot apply MOD prompt update: ${targetModId} is not available for this template.`)
        return
      }

      setModId(targetModId)
      setModPromptOverrides((prev) => ({
        ...prev,
        [targetModId]: incomingPromptUpdate.prompt,
      }))
      setDebouncedModPromptOverrides((prev) => ({
        ...prev,
        [targetModId]: incomingPromptUpdate.prompt,
      }))
      setError(null)
      setStatus(
        incomingPromptUpdate.summary?.trim()
          ? `Applied Claude MOD update (${targetModId}): ${incomingPromptUpdate.summary.trim()}`
          : `Applied Claude prompt update to ${targetModId} MOD draft`
      )
      return
    }

    setCustomPrompt(incomingPromptUpdate.prompt)
    setDebouncedPromptDraft(incomingPromptUpdate.prompt)
    setError(null)
    setStatus(
      incomingPromptUpdate.summary?.trim()
        ? `Applied Claude update: ${incomingPromptUpdate.summary.trim()}`
        : 'Applied Claude prompt update to the draft'
    )
  }, [incomingPromptUpdate, availableModIds, selectedModId])

  const handleNew = () => {
    if (selectedCustomAgent) {
      setCloneSourceAgentId(selectedCustomAgent.id)
    }
    setSelectedCustomAgentId('')
    setSelectedVisibility('private')
    setSaveNotes('')
    setStatus('Creating a new custom agent draft')
  }

  const reloadAfterSave = async (keepId?: string) => {
    const response = await listCustomAgents()
    setCustomAgents(response.custom_agents)
    if (keepId) {
      setSelectedCustomAgentId(keepId)
      setCloneSourceAgentId(keepId)
    } else if (response.custom_agents.length > 0) {
      const templateAlignedAgentId = getTemplateAlignedAgentId(response.custom_agents)
      setSelectedCustomAgentId(templateAlignedAgentId)
      setCloneSourceAgentId(templateAlignedAgentId || response.custom_agents[0].id)
    } else {
      setSelectedCustomAgentId('')
      setCloneSourceAgentId('')
    }
    await refreshAgentMetadata()
  }

  const handleSave = async (options?: { forceCreate?: boolean; nameOverride?: string }) => {
    const forceCreate = options?.forceCreate ?? false
    const nameToSave = (options?.nameOverride ?? name).trim()

    if (gettingStartedMode === 'template' && !parentAgentId && !selectedCustomAgentId) {
      setError('Please select a template')
      return
    }
    if (gettingStartedMode === 'clone' && !cloneSourceAgentId && !selectedCustomAgentId) {
      setError('Please select an agent to clone')
      return
    }
    if (!selectedModelId.trim()) {
      setError('Please select a model')
      return
    }
    if (!nameToSave) {
      setError('Please enter a custom agent name')
      return
    }
    if (!customPrompt.trim()) {
      setError('Prompt text cannot be empty')
      return
    }
    const updatingExistingAgent = !forceCreate && Boolean(selectedCustomAgentId)
    const existingToolCount = selectedCustomAgent?.tool_ids?.length || 0
    if (updatingExistingAgent && existingToolCount > 0 && selectedToolIds.length === 0) {
      setError(
        'Cannot save this agent with no tools selected because it previously had attached tools. '
        + 'Re-attach at least one tool or use Save As to intentionally create a tool-free copy.'
      )
      return
    }

    setSaving(true)
    setError(null)
    setStatus(null)

    try {
      const shouldCreate = forceCreate || !selectedCustomAgentId
      if (!shouldCreate && selectedCustomAgentId) {
        let updated = await updateCustomAgent(selectedCustomAgentId, {
          name: nameToSave,
          description: description.trim() || undefined,
          custom_prompt: customPrompt,
          mod_prompt_overrides: modPromptOverrides,
          include_mod_rules: includeModRules,
          model_id: selectedModelId,
          model_reasoning: selectedModelReasoning || undefined,
          tool_ids: selectedToolIds,
          output_schema_key: outputSchemaKey || undefined,
          icon: icon || undefined,
          notes: saveNotes.trim() || undefined,
        })
        const currentVisibility = updated.visibility === 'project' ? 'project' : 'private'
        if (currentVisibility !== selectedVisibility) {
          updated = await setCustomAgentVisibility(updated.agent_id, selectedVisibility)
        }
        await reloadAfterSave(updated.id)
        setStatus(`Updated "${updated.name}"`)
      } else {
        const templateSource = selectedCustomAgent?.template_source
          || (gettingStartedMode === 'template'
            ? parentAgentId
            : (gettingStartedMode === 'clone' ? selectedCloneSource?.template_source : undefined))
        let created = await createCustomAgent({
          template_source: templateSource || undefined,
          name: nameToSave,
          description: description.trim() || undefined,
          custom_prompt: customPrompt,
          mod_prompt_overrides: modPromptOverrides,
          include_mod_rules: includeModRules,
          model_id: selectedModelId,
          model_reasoning: selectedModelReasoning || undefined,
          tool_ids: selectedToolIds,
          output_schema_key: outputSchemaKey || undefined,
          icon: icon || undefined,
        })
        if (selectedVisibility === 'project') {
          created = await setCustomAgentVisibility(created.agent_id, 'project')
        }
        await reloadAfterSave(created.id)
        setStatus(forceCreate ? `Saved as "${created.name}"` : `Created "${created.name}"`)
      }
      setSaveNotes('')
      if (forceCreate) {
        setSaveAsName('')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save custom agent')
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteById = async (agent: CustomAgent) => {
    setSaving(true)
    setError(null)
    try {
      await deleteCustomAgent(agent.id)
      await reloadAfterSave()
      if (selectedCustomAgentId === agent.id) {
        const hasRemaining = customAgents.some((candidate) => candidate.id !== agent.id)
        if (!hasRemaining) handleNew()
      }
      setStatus(`Deleted "${agent.name}"`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete custom agent')
    } finally {
      setSaving(false)
    }
  }

  const handleRevert = async (version: number) => {
    if (!selectedCustomAgentId) return
    setSaving(true)
    setError(null)
    try {
      const reverted = await revertCustomAgentVersion(selectedCustomAgentId, version, saveNotes || undefined)
      await reloadAfterSave(reverted.id)
      setStatus(`Reverted to version ${version}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revert version')
    } finally {
      setSaving(false)
    }
  }

  const handleSelectedModPromptChange = (value: string) => {
    if (!selectedModId) return
    setModPromptOverrides((prev) => {
      const next = { ...prev }
      if (value === selectedModBasePrompt || (!value.trim() && !selectedModBasePrompt.trim())) {
        delete next[selectedModId]
      } else {
        next[selectedModId] = value
      }
      return next
    })
  }

  const handleResetSelectedModPrompt = () => {
    if (!selectedModId) return
    setModPromptOverrides((prev) => {
      if (!Object.prototype.hasOwnProperty.call(prev, selectedModId)) return prev
      const next = { ...prev }
      delete next[selectedModId]
      return next
    })
  }

  const handleToggleTool = (toolKey: string) => {
    const policy = toolPolicyByKey.get(toolKey)
    if (policy && !policy.allow_attach) return

    setSelectedToolIds((prev) => {
      if (prev.includes(toolKey)) {
        return prev.filter((existing) => existing !== toolKey)
      }
      return [...prev, toolKey]
    })
  }

  const handleRemoveTool = (toolKey: string) => {
    setSelectedToolIds((prev) => prev.filter((existing) => existing !== toolKey))
  }

  const handleOpenToolLibrary = () => {
    setToolLibraryDialogOpen(true)
  }

  const handleCloseToolLibrary = () => {
    setToolLibraryDialogOpen(false)
    setToolLibrarySearch('')
    setToolLibraryCategory('all')
  }

  const refreshToolIdeas = async () => {
    const response = await listToolIdeaRequests()
    setToolIdeaRequests(response.tool_ideas)
  }

  const handleAskClaudeForTool = () => {
    const targetName = selectedCustomAgent?.name || name.trim() || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
    const targetId = selectedCustomAgent?.agent_id || parentAgentId || 'unsaved_draft'
    const attachedTools = selectedToolIds.length > 0 ? selectedToolIds.join(', ') : 'none'
    const message = `I need help designing a NEW tool request for Agent Workshop.\n\nContext:\n- Agent draft: ${targetName}\n- Agent ID: ${targetId}\n- Attached tools: ${attachedTools}\n\nPlease guide me with focused questions and help me produce:\n1. A concise request title\n2. Clear problem statement\n3. Required inputs\n4. Expected output format\n5. One concrete usage example\n\nWhen we finish, provide a final polished request that I can submit to developers.\n\n[Request ID: ${Date.now()}]`
    onVerifyRequest?.(message)
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
    if (!toolIdeaTitle.trim()) {
      setError('Please enter a tool request title')
      return
    }
    if (!toolIdeaDescription.trim()) {
      setError('Please enter a tool request description')
      return
    }

    setToolIdeaSubmitting(true)
    setError(null)
    try {
      const ideationTranscript = opusConversation
        .filter((entry) => Boolean(entry.content && entry.content.trim()))
        .slice(-30)

      const created = await submitToolIdeaRequest({
        title: toolIdeaTitle.trim(),
        description: toolIdeaDescription.trim(),
        opus_conversation: ideationTranscript,
      })
      await refreshToolIdeas()
      setStatus(`Submitted tool request "${created.title}"`)
      handleCloseToolIdeaDialog()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit tool request')
    } finally {
      setToolIdeaSubmitting(false)
    }
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
    await handleSave({ forceCreate: true, nameOverride: trimmedName })
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
    const modPart = selectedModId ? `Selected MOD: ${selectedModId}` : 'Selected MOD: none'
    const message = `Discuss my Agent Workshop draft for "${targetName}".\n\nPlease help with:\n1. Prompt quality and clarity issues\n2. Risky or ambiguous instructions\n3. Concrete edits to improve behavior\n4. Suggested flow-based validation tests\n\nAgent ID: ${targetId}\n${modPart}\n\n[Request ID: ${Date.now()}]`

    onVerifyRequest?.(message)
  }

  const handleModelChange = (modelId: string) => {
    setSelectedModelId(modelId)
    setSelectedModelReasoning(resolveReasoningSelection(modelOptions, modelId))
  }

  const handleAskClaudeAboutModels = () => {
    const targetName = selectedCustomAgent?.name || name.trim() || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
    const modelLines = modelOptions.map((model) => {
      const reasoning = model.reasoning_options.length > 0
        ? `Reasoning: ${model.reasoning_options.join(', ')} (default: ${model.default_reasoning || 'none'})`
        : 'Reasoning: n/a'
      return `- ${model.name} [${model.model_id}] via ${model.provider}\n  Guidance: ${model.guidance || model.description || 'n/a'}\n  ${reasoning}`
    }).join('\n')

    const message = `Help me choose the best model settings for my Agent Workshop draft.\n\nAgent draft: ${targetName}\nCurrent model: ${selectedModelId || 'none'}\nCurrent reasoning: ${selectedModelReasoning || 'none'}\nAttached tools: ${selectedToolIds.length > 0 ? selectedToolIds.join(', ') : 'none'}\n\nAvailable models:\n${modelLines}\n\nRecommendation policy to follow unless my use case says otherwise:\n- Prefer openai/gpt-oss-120b (Groq) for database lookup and validation workflows.\n- Prefer gpt-5.4 with medium reasoning for complex PDF extraction and deep thinking.\n- Use gpt-5-mini as a faster middle-ground for iterative drafting.\n\nPlease:\n1. Ask 1-3 focused questions to understand my use case\n2. Recommend a model and (if applicable) reasoning level\n3. Explain tradeoffs in plain curator-friendly language\n4. Give one backup model choice\n\n[Request ID: ${Date.now()}]`
    onVerifyRequest?.(message)
    setStatus('Opened model-selection discussion with Claude')
  }

  const handleDiscussPromptChangesWithClaude = () => {
    const targetName = selectedCustomAgent?.name || name.trim() || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
    const targetId = selectedCustomAgent?.agent_id || parentAgentId || 'unknown'
    const modPart = selectedModId ? `Selected MOD: ${selectedModId}` : 'Selected MOD: none'
    const message = `Help me improve the SYSTEM PROMPT for "${targetName}".\n\nPlease:\n1. Identify unclear, conflicting, or risky instructions.\n2. Propose concrete edits focused on behavior and extraction quality.\n3. Explain why each suggested edit helps.\n4. Keep changes minimal unless a full rewrite is truly needed.\n\nAgent ID: ${targetId}\n${modPart}\n\n[Request ID: ${Date.now()}]`

    onVerifyRequest?.(message)
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
          <StyledMenuItem onClick={() => void handleSave()} disabled={saving}>
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

      <Box sx={{ p: 2.5, overflow: 'auto' }}>
        <Stack spacing={3}>
          {error && <Alert severity="error" sx={{ borderRadius: 2 }}>{error}</Alert>}
          {status && <Alert severity="success" sx={{ borderRadius: 2 }}>{status}</Alert>}

          {selectedCustomAgent && !selectedCustomAgent.parent_exists && (
            <Alert severity="error" sx={{ borderRadius: 2 }}>
              Template source is unavailable, so this custom agent cannot be executed.
            </Alert>
          )}

          {/* ── Section 1: Identity & Configuration ── */}
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
                      setGettingStartedMode(value as GettingStartedMode)
                      setSelectedCustomAgentId('')
                    }
                  }}
                >
                  <ToggleButton value="template">Template</ToggleButton>
                  <ToggleButton value="scratch">Scratch</ToggleButton>
                  <ToggleButton value="clone">Clone</ToggleButton>
                </StyledToggleButtonGroup>
              </Box>

              {gettingStartedMode === 'template' && (
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

              <Divider sx={{ opacity: 0.5 }} />

              {/* Name + Icon row */}
              <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
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
                size="small"
                label="Description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Brief description of what this agent does"
              />

              <Divider sx={{ opacity: 0.5 }} />

              {/* Model / Visibility / Output Schema */}
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Typography variant="caption" color="text.secondary">
                  Model guidance
                </Typography>
                <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{MODEL_HELP_TEXT}</span>} placement="top">
                  <IconButton size="small" sx={{ p: 0.25 }}>
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
                    {modelOptions.map((model) => (
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
                <TextField
                  size="small"
                  label="Output Schema Key"
                  value={outputSchemaKey}
                  onChange={(event) => setOutputSchemaKey(event.target.value)}
                  placeholder="Optional"
                  sx={{ minWidth: 180, flex: 1 }}
                />
              </Box>

              {selectedModelOption && (
                <Box
                  sx={{
                    border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.6)}`,
                    borderRadius: 1.5,
                    p: 1.5,
                    backgroundColor: (theme) => alpha(theme.palette.background.default, 0.35),
                  }}
                >
                  <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={1.5}>
                    <Box>
                      <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>
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
                              <Chip key={item} size="small" color="success" variant="outlined" label={item} />
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
                              <Chip key={item} size="small" color="warning" variant="outlined" label={item} />
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

          {/* ── Section 2: System Prompt ── */}
          <SectionCard elevation={0}>
            <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1.5} sx={{ mb: 2 }}>
              <SectionHeader sx={{ mb: 0 }}>System Prompt</SectionHeader>
              {onVerifyRequest && (
                <Button size="small" variant="outlined" onClick={handleDiscussPromptChangesWithClaude}>
                  Discuss prompt changes with Claude
                </Button>
              )}
            </Stack>
            <Stack spacing={1}>
              <StyledAccordion defaultExpanded={false}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>Main Prompt</Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <TextField
                    fullWidth
                    multiline
                    minRows={12}
                    value={customPrompt}
                    onChange={(event) => setCustomPrompt(event.target.value)}
                    placeholder="Enter the system prompt for this agent..."
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

              <StyledAccordion defaultExpanded={false}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="subtitle2" sx={{ fontSize: '0.85rem' }}>MOD Prompt Overrides</Typography>
                    {hasAnyModOverrides && (
                      <Chip size="small" label={`${Object.keys(modPromptOverrides).length} override${Object.keys(modPromptOverrides).length !== 1 ? 's' : ''}`} color="warning" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                    )}
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <Stack spacing={1.5}>
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <FormControlLabel
                        sx={{ ml: 0, mr: 0 }}
                        control={
                          <Switch
                            size="small"
                            checked={includeModRules}
                            onChange={(event) => setIncludeModRules(event.target.checked)}
                          />
                        }
                        label={
                          <Typography variant="body2" color="text.secondary">
                            Add MOD prompts at runtime
                          </Typography>
                        }
                      />
                      <Tooltip
                        title="When enabled, MOD-specific prompts/rules are included at runtime for this agent."
                        placement="top"
                      >
                        <IconButton size="small" sx={{ p: 0.25 }} aria-label="MOD prompt runtime help">
                          <HelpOutlineIcon sx={{ fontSize: 14 }} />
                        </IconButton>
                      </Tooltip>
                    </Stack>

                    {availableModIds.length === 0 ? (
                      <Typography variant="body2" color="text.secondary">
                        This template has no MOD-specific prompts to override.
                      </Typography>
                    ) : (
                      <Stack spacing={1.5}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Select
                            size="small"
                            value={modId}
                            onChange={(event) => setModId(event.target.value)}
                            sx={{ minWidth: 160 }}
                          >
                            {availableModIds.map((availableModId) => (
                              <MenuItem key={availableModId} value={availableModId}>
                                {availableModId}
                              </MenuItem>
                            ))}
                          </Select>
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={handleResetSelectedModPrompt}
                            disabled={!hasSelectedModOverride}
                          >
                            Reset to Template
                          </Button>
                        </Stack>
                        <TextField
                          fullWidth
                          multiline
                          minRows={8}
                          label={selectedModId ? `${selectedModId} Prompt` : 'MOD Prompt'}
                          value={selectedModPrompt}
                          onChange={(event) => handleSelectedModPromptChange(event.target.value)}
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
                          {hasSelectedModOverride
                            ? `Custom override active for ${selectedModId}.`
                            : `Using template ${selectedModId} prompt content.`}
                          {hasAnyModOverrides ? ` Total overrides: ${Object.keys(modPromptOverrides).length}.` : ''}
                        </Typography>
                      </Stack>
                    )}
                  </Stack>
                </AccordionDetails>
              </StyledAccordion>
            </Stack>
          </SectionCard>

          {/* ── Section 3: Advanced Settings ── */}
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
                            onDelete={() => handleRemoveTool(toolId)}
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
                            onClick={() => handleRevert(version.version)}
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
                          setSelectedCustomAgentId(agent.id)
                          handleOpenDialogClose()
                          setStatus(`Opened "${agent.name}"`)
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
                            setSelectedCustomAgentId(agent.id)
                            setStatus(`Opened "${agent.name}"`)
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
                            handleToggleTool(tool.tool_key)
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
