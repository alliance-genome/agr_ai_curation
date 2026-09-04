import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type {
  AgentTemplate,
  AgentWorkshopContext,
  CustomAgent,
  CustomAgentVersion,
  GroupOption,
  ModelOption,
  PromptCatalog,
  PromptInfo,
  ToolIdeaConversationEntry,
  ToolIdeaRequest,
  ToolLibraryItem,
  WorkshopPromptUpdateRequest,
} from '@/types/promptExplorer'
import {
  createCustomAgent,
  deleteCustomAgent,
  fetchAgentTemplates,
  fetchModelOptions,
  fetchToolLibrary,
  listCustomAgentVersions,
  listCustomAgents,
  listToolIdeaRequests,
  revertCustomAgentVersion,
  setCustomAgentVisibility,
  submitToolIdeaRequest,
  updateCustomAgent,
} from '@/services/agentStudioService'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import { useAuth } from '@/contexts/AuthContext'

import {
  DEFAULT_AGENT_ICON,
  FALLBACK_ICON_OPTIONS,
  areStringArraysEqual,
  areStringRecordsEqual,
  cloneDraftName,
  computeDirtyState,
  joinPromptLayers,
  resolveModelSelection,
  resolveParentBasePrompt,
  resolveReasoningSelection,
  resolveUserGroupIds,
  type DraftDirtyState,
  type DraftFields,
  type GettingStartedMode,
  type SaveState,
  type WorkshopVisibility,
} from './workshopDraftUtils'

export interface SaveOptions {
  forceCreate?: boolean
  nameOverride?: string
  notes?: string
}

export interface UseWorkshopDraftArgs {
  catalog: PromptCatalog
  initialParentAgentId?: string | null
  initialCustomAgentId?: string | null
  onContextChange?: (context: AgentWorkshopContext) => void
  incomingPromptUpdate?: WorkshopPromptUpdateRequest | null
}

export interface WorkshopDraft {
  // Loaded options
  modelOptions: ModelOption[]
  toolLibrary: ToolLibraryItem[]
  templateOptions: AgentTemplate[]
  groupOptions: GroupOption[]
  loading: boolean

  // Origin
  gettingStartedMode: GettingStartedMode
  parentAgentId: string
  setParentAgentId: (agentId: string) => void
  parentAgent: PromptInfo | undefined
  selectedTemplate: AgentTemplate | undefined
  /** The saved agent names a template that is no longer installed. */
  templateMissing: boolean
  customAgents: CustomAgent[]
  selectedCustomAgentId: string
  selectedCustomAgent: CustomAgent | undefined
  selectCustomAgent: (agentId: string) => void
  cloneSourceAgentId: string
  setCloneSourceAgentId: (agentId: string) => void
  selectedCloneSource: CustomAgent | undefined
  isNewDraft: boolean

  // Fields
  name: string
  setName: (value: string) => void
  description: string
  setDescription: (value: string) => void
  icon: string
  setIcon: (value: string) => void
  iconOptions: string[]
  customPrompt: string
  setCustomPrompt: (value: string) => void
  resetCustomPromptToTemplate: () => void
  groupPromptOverrides: Record<string, string>
  includeGroupRules: boolean
  setIncludeGroupRules: (value: boolean) => void
  selectedVisibility: WorkshopVisibility
  setSelectedVisibility: (value: WorkshopVisibility) => void
  selectedAllowedGroupIds: string[]
  setSelectedAllowedGroupIds: (value: string[]) => void
  selectedModelId: string
  handleModelChange: (modelId: string) => void
  selectedModelReasoning: string
  setSelectedModelReasoning: (value: string) => void
  selectedToolIds: string[]
  removeTool: (toolKey: string) => void
  applyToolSelection: (toolIds: string[]) => void

  // Groups
  setGroupId: (value: string) => void
  availableGroupIds: string[]
  selectedGroupId: string
  selectedGroupPrompt: string
  hasSelectedGroupOverride: boolean
  handleSelectedGroupPromptChange: (value: string) => void
  handleResetSelectedGroupPrompt: () => void
  loggedInGroupIds: string[]
  loggedInAsLabel: string
  currentUserGroupIds: string[]
  inheritedAllowedGroupIds: string[]
  selectableGroupOptions: GroupOption[]

  // Prompt layers
  parentCorePrompt: string
  parentGeneratedContract: string
  parentBasePrompt: string
  overlayStatus: CustomAgent['custom_prompt_overlay_status']
  overlayWarning: string

  // Model
  selectedModelOption: ModelOption | null
  selectedModelReasoningDescription: string

  // Envelope
  domainEnvelopeAgentId: string

  // Tools
  toolIdeaRequests: ToolIdeaRequest[]
  toolIdeasLoading: boolean
  submitToolIdea: (title: string, description: string, conversation: ToolIdeaConversationEntry[]) => Promise<boolean>
  toolIdeaSubmitting: boolean

  // Versions
  versions: CustomAgentVersion[]

  // Save state
  saving: boolean
  saveState: SaveState
  lastSavedAt: number | null
  status: string | null
  error: string | null
  setError: (value: string | null) => void
  setStatus: (value: string | null) => void
  dirty: DraftDirtyState
  canSave: boolean

  // Actions
  handleNew: () => void
  /** Detach from the open agent (if any) and start a fresh draft in the given mode. */
  startDraft: (mode: GettingStartedMode) => void
  handleSave: (options?: SaveOptions, selfExclusionConfirmed?: boolean) => Promise<void>
  handleDeleteById: (agent: CustomAgent) => Promise<void>
  handleRevert: (version: number) => Promise<void>
  selfExclusionPrompt: SaveOptions | null
  confirmSelfExclusion: () => void
  cancelSelfExclusion: () => void
}

/**
 * Keep the same object while the record's id and updated_at are unchanged, so a
 * background list refresh does not re-hydrate the draft and discard unsaved edits.
 */
function useStableAgentRecord(candidate: CustomAgent | undefined): CustomAgent | undefined {
  const ref = useRef<CustomAgent | undefined>(undefined)
  if (!candidate) {
    ref.current = undefined
  } else if (!ref.current || ref.current.id !== candidate.id || ref.current.updated_at !== candidate.updated_at) {
    ref.current = candidate
  }
  return ref.current
}

export function useWorkshopDraft({
  catalog,
  initialParentAgentId,
  initialCustomAgentId,
  onContextChange,
  incomingPromptUpdate = null,
}: UseWorkshopDraftArgs): WorkshopDraft {
  const { agents: agentMetadata, refresh: refreshAgentMetadata } = useAgentMetadata()
  const { user: authUser } = useAuth()

  const [parentAgentId, setParentAgentId] = useState('')
  const [gettingStartedMode, setGettingStartedMode] = useState<GettingStartedMode>('template')
  const [customAgents, setCustomAgents] = useState<CustomAgent[]>([])
  const [selectedCustomAgentId, setSelectedCustomAgentId] = useState<string>('')
  const [cloneSourceAgentId, setCloneSourceAgentId] = useState<string>('')
  const [versions, setVersions] = useState<CustomAgentVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [workshopOptionsLoaded, setWorkshopOptionsLoaded] = useState(false)
  const [hydrationVersion, setHydrationVersion] = useState(0)
  const [saving, setSaving] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')
  const [debouncedPromptDraft, setDebouncedPromptDraft] = useState('')
  const [groupPromptOverrides, setGroupPromptOverrides] = useState<Record<string, string>>({})
  const [debouncedGroupPromptOverrides, setDebouncedGroupPromptOverrides] = useState<Record<string, string>>({})
  const [includeGroupRules, setIncludeGroupRules] = useState(true)
  const [selectedVisibility, setSelectedVisibility] = useState<WorkshopVisibility>('private')
  const [selectedAllowedGroupIds, setSelectedAllowedGroupIds] = useState<string[]>([])
  const [selectedModelId, setSelectedModelId] = useState('')
  const [selectedModelReasoning, setSelectedModelReasoning] = useState('')
  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([])
  const [outputSchemaKey, setOutputSchemaKey] = useState('')
  const [icon, setIcon] = useState(DEFAULT_AGENT_ICON)
  const [groupId, setGroupId] = useState('')
  const [savedSnapshot, setSavedSnapshot] = useState<DraftFields | null>(null)

  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [toolLibrary, setToolLibrary] = useState<ToolLibraryItem[]>([])
  const [templateOptions, setTemplateOptions] = useState<AgentTemplate[]>([])
  const [groupOptions, setGroupOptions] = useState<GroupOption[]>([])
  const [toolIdeaRequests, setToolIdeaRequests] = useState<ToolIdeaRequest[]>([])
  const [toolIdeasLoading, setToolIdeasLoading] = useState(false)
  const [toolIdeaSubmitting, setToolIdeaSubmitting] = useState(false)
  const [selfExclusionPrompt, setSelfExclusionPrompt] = useState<SaveOptions | null>(null)

  const appliedInitialCustomAgentId = useRef<string | null>(null)
  const refreshAttemptedForInitialCustomAgentId = useRef<string | null>(null)
  const appliedPromptUpdateId = useRef<number | null>(null)
  const appliedPromptUpdateHydrationVersion = useRef<number>(-1)
  const customAgentsLoadingRef = useRef(false)

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
  const selectedCustomAgent = useStableAgentRecord(
    customAgents.find((agent) => agent.id === selectedCustomAgentId)
  )
  const selectedCloneSource = useStableAgentRecord(
    customAgents.find((agent) => agent.id === cloneSourceAgentId)
  )
  const templateMissing = useMemo(() => {
    const source = selectedCustomAgent?.template_source
    if (!source || !workshopOptionsLoaded) return false
    return !templateOptions.some((template) => template.agent_id === source)
  }, [selectedCustomAgent?.template_source, templateOptions, workshopOptionsLoaded])

  const groupRuleSourceAgent = useMemo(() => {
    if (selectedCustomAgent) {
      if (!selectedCustomAgent.template_source) return null
      return parentAgents.find((agent) => agent.agent_id === selectedCustomAgent.template_source) || null
    }
    if (gettingStartedMode === 'clone') {
      if (!selectedCloneSource?.template_source) return null
      return parentAgents.find((agent) => agent.agent_id === selectedCloneSource.template_source) || null
    }
    if (gettingStartedMode === 'template') {
      if (!parentAgentId) return null
      return parentAgents.find((agent) => agent.agent_id === parentAgentId) || null
    }
    return null
  }, [gettingStartedMode, parentAgentId, parentAgents, selectedCloneSource?.template_source, selectedCustomAgent])

  const availableGroupIds = useMemo(
    () => Object.keys(groupRuleSourceAgent?.group_rules || {}).sort(),
    [groupRuleSourceAgent]
  )
  const loggedInGroupIds = useMemo(
    () => resolveUserGroupIds(
      authUser?.groups?.length ? authUser.groups : authUser?.providerGroups,
      availableGroupIds
    ),
    [authUser?.groups, authUser?.providerGroups, availableGroupIds]
  )
  const currentUserGroupIds = useMemo(
    () => Array.from(new Set((authUser?.groups || []).map((group) => group.trim().toUpperCase()).filter(Boolean))),
    [authUser?.groups]
  )

  const inheritedAllowedGroupIds = useMemo(() => {
    if (selectedCustomAgent) {
      return selectedCustomAgent.inherited_allowed_group_ids
    }
    if (gettingStartedMode === 'clone') {
      return selectedCloneSource?.allowed_group_ids || []
    }
    return selectedTemplate?.allowed_group_ids || []
  }, [gettingStartedMode, selectedCloneSource?.allowed_group_ids, selectedCustomAgent, selectedTemplate?.allowed_group_ids])

  const selectableGroupOptions = useMemo(() => {
    if (inheritedAllowedGroupIds.length === 0) return groupOptions
    const inherited = new Set(inheritedAllowedGroupIds)
    return groupOptions.filter((group) => inherited.has(group.group_id))
  }, [groupOptions, inheritedAllowedGroupIds])

  const selectedGroupId = useMemo(() => groupId.trim().toUpperCase(), [groupId])

  const selectedGroupBasePrompt = useMemo(() => {
    if (!selectedGroupId) return ''
    return groupRuleSourceAgent?.group_rules[selectedGroupId]?.content || ''
  }, [groupRuleSourceAgent, selectedGroupId])

  const selectedGroupPrompt = useMemo(() => {
    if (!selectedGroupId) return ''
    if (Object.prototype.hasOwnProperty.call(groupPromptOverrides, selectedGroupId)) {
      return groupPromptOverrides[selectedGroupId]
    }
    return selectedGroupBasePrompt
  }, [groupPromptOverrides, selectedGroupId, selectedGroupBasePrompt])

  const selectedGroupPromptForContext = useMemo(() => {
    if (!selectedGroupId) return undefined
    if (Object.prototype.hasOwnProperty.call(debouncedGroupPromptOverrides, selectedGroupId)) {
      return debouncedGroupPromptOverrides[selectedGroupId]
    }
    return groupRuleSourceAgent?.group_rules[selectedGroupId]?.content
  }, [debouncedGroupPromptOverrides, groupRuleSourceAgent, selectedGroupId])

  const parentCorePrompt = joinPromptLayers(parentAgent, 'core_static')
  const parentGeneratedContract = joinPromptLayers(parentAgent, 'core_generated')
  const parentBasePrompt = resolveParentBasePrompt(parentAgent)
  const overlayStatus = selectedCustomAgent?.custom_prompt_overlay_status
  const overlayWarning = selectedCustomAgent?.custom_prompt_warning || ''
  const loggedInAsLabel = authUser?.name || authUser?.email || authUser?.uid || 'the current user'

  const hasSelectedGroupOverride = useMemo(
    () => Boolean(selectedGroupId && Object.prototype.hasOwnProperty.call(groupPromptOverrides, selectedGroupId)),
    [groupPromptOverrides, selectedGroupId]
  )

  const iconOptions = useMemo(() => {
    const discovered = Object.values(agentMetadata)
      .map((agent) => agent.icon)
      .filter((candidate): candidate is string => Boolean(candidate && candidate.trim()))
    return Array.from(new Set([...FALLBACK_ICON_OPTIONS, ...discovered, icon || DEFAULT_AGENT_ICON]))
  }, [agentMetadata, icon])

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

  const domainEnvelopeAgentId = useMemo(() => {
    if (selectedCustomAgent?.template_source) return selectedCustomAgent.template_source
    if (gettingStartedMode === 'clone' && selectedCloneSource?.template_source) {
      return selectedCloneSource.template_source
    }
    if (gettingStartedMode === 'template') return parentAgentId
    return ''
  }, [gettingStartedMode, parentAgentId, selectedCloneSource?.template_source, selectedCustomAgent?.template_source])

  const toolPolicyByKey = useMemo(
    () => new Map(toolLibrary.map((tool) => [tool.tool_key, tool])),
    [toolLibrary]
  )

  const currentFields = useMemo<DraftFields>(() => ({
    name,
    description,
    customPrompt,
    groupPromptOverrides,
    includeGroupRules,
    visibility: selectedVisibility,
    allowedGroupIds: selectedAllowedGroupIds,
    modelId: selectedModelId,
    modelReasoning: selectedModelReasoning,
    toolIds: selectedToolIds,
    outputSchemaKey,
    icon,
  }), [
    name,
    description,
    customPrompt,
    groupPromptOverrides,
    includeGroupRules,
    selectedVisibility,
    selectedAllowedGroupIds,
    selectedModelId,
    selectedModelReasoning,
    selectedToolIds,
    outputSchemaKey,
    icon,
  ])

  const dirty = useMemo(() => computeDirtyState(currentFields, savedSnapshot), [currentFields, savedSnapshot])
  const isNewDraft = !selectedCustomAgent
  const canSave = !saving && (isNewDraft || dirty.any)

  const applyDraft = useCallback((fields: DraftFields) => {
    setName(fields.name)
    setDescription(fields.description)
    setCustomPrompt(fields.customPrompt)
    setDebouncedPromptDraft(fields.customPrompt)
    setGroupPromptOverrides(fields.groupPromptOverrides)
    setDebouncedGroupPromptOverrides(fields.groupPromptOverrides)
    setIncludeGroupRules(fields.includeGroupRules)
    setSelectedAllowedGroupIds(fields.allowedGroupIds)
    setSelectedVisibility(fields.visibility)
    setSelectedModelId(fields.modelId)
    setSelectedModelReasoning(fields.modelReasoning)
    setSelectedToolIds(fields.toolIds)
    setOutputSchemaKey(fields.outputSchemaKey)
    setIcon(fields.icon)
    setSavedSnapshot(fields)
    setHydrationVersion((prev) => prev + 1)
  }, [])

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
      setWorkshopOptionsLoaded(false)
      try {
        const [models, tools, workshopMetadata] = await Promise.all([
          fetchModelOptions(),
          fetchToolLibrary(),
          fetchAgentTemplates(),
        ])
        setModelOptions(models)
        setToolLibrary(tools)
        setTemplateOptions(workshopMetadata.templates)
        setGroupOptions(workshopMetadata.group_options)
        if (models.length === 0) {
          setError('No model options are configured. Add entries in config/models.yaml before creating agents.')
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load workshop options')
      } finally {
        setWorkshopOptionsLoaded(true)
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
    const resolvedReasoning = resolveReasoningSelection(modelOptions, selectedModelId, selectedModelReasoning)
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
    customAgentsLoadingRef.current = true
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
        setSelectedCustomAgentId((prev) => (
          response.custom_agents.some((agent) => agent.id === prev) ? prev : ''
        ))
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
      customAgentsLoadingRef.current = false
      if (!silent) {
        setLoading(false)
      }
    }
  }, [getTemplateAlignedAgentId])

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
    // Hydrate only once model and template options are known, so the draft is
    // built once and a later options arrival cannot overwrite curator edits.
    if (!workshopOptionsLoaded) return
    if (!selectedCustomAgent) {
      if (gettingStartedMode === 'clone' && selectedCloneSource) {
        const cloneModelId = resolveModelSelection(modelOptions, defaultModelId, selectedCloneSource.model_id)
        applyDraft({
          name: cloneDraftName(selectedCloneSource),
          description: selectedCloneSource.description || '',
          customPrompt: selectedCloneSource.custom_prompt,
          groupPromptOverrides: selectedCloneSource.group_prompt_overrides || {},
          includeGroupRules: selectedCloneSource.include_group_rules,
          allowedGroupIds: selectedCloneSource.allowed_group_ids || [],
          visibility: 'private',
          modelId: cloneModelId,
          modelReasoning: resolveReasoningSelection(modelOptions, cloneModelId, selectedCloneSource.model_reasoning),
          toolIds: selectedCloneSource.tool_ids || [],
          outputSchemaKey: selectedCloneSource.output_schema_key || '',
          icon: selectedCloneSource.icon || DEFAULT_AGENT_ICON,
        })
        if (selectedCloneSource.template_source) {
          setParentAgentId(selectedCloneSource.template_source)
        }
        return
      }

      if (gettingStartedMode === 'scratch') {
        applyDraft({
          name: '',
          description: '',
          customPrompt: '',
          groupPromptOverrides: {},
          includeGroupRules: false,
          allowedGroupIds: [],
          visibility: 'private',
          modelId: defaultModelId,
          modelReasoning: resolveReasoningSelection(modelOptions, defaultModelId),
          toolIds: [],
          outputSchemaKey: '',
          icon: DEFAULT_AGENT_ICON,
        })
        return
      }

      const templateModelId = resolveModelSelection(modelOptions, defaultModelId, selectedTemplate?.model_id)
      applyDraft({
        name: parentAgent ? `${parentAgent.agent_name} (Custom)` : '',
        description: '',
        customPrompt: parentBasePrompt,
        groupPromptOverrides: {},
        includeGroupRules: true,
        allowedGroupIds: selectedTemplate?.allowed_group_ids || [],
        visibility: 'private',
        modelId: templateModelId,
        modelReasoning: resolveReasoningSelection(modelOptions, templateModelId),
        toolIds: selectedTemplate?.tool_ids || [],
        outputSchemaKey: selectedTemplate?.output_schema_key || '',
        icon: DEFAULT_AGENT_ICON,
      })
      return
    }

    const customModelId = resolveModelSelection(modelOptions, defaultModelId, selectedCustomAgent.model_id)
    applyDraft({
      name: selectedCustomAgent.name,
      description: selectedCustomAgent.description || '',
      customPrompt: selectedCustomAgent.custom_prompt || parentBasePrompt,
      groupPromptOverrides: selectedCustomAgent.group_prompt_overrides || {},
      includeGroupRules: selectedCustomAgent.include_group_rules,
      allowedGroupIds: selectedCustomAgent.allowed_group_ids || [],
      visibility: selectedCustomAgent.visibility === 'project' ? 'project' : 'private',
      modelId: customModelId,
      modelReasoning: resolveReasoningSelection(modelOptions, customModelId, selectedCustomAgent.model_reasoning),
      toolIds: selectedCustomAgent.tool_ids || [],
      outputSchemaKey: selectedCustomAgent.output_schema_key || '',
      icon: selectedCustomAgent.icon || DEFAULT_AGENT_ICON,
    })
    if (selectedCustomAgent.template_source) {
      setParentAgentId(selectedCustomAgent.template_source)
    }
  }, [
    applyDraft,
    workshopOptionsLoaded,
    modelOptions,
    defaultModelId,
    gettingStartedMode,
    parentAgent,
    parentBasePrompt,
    selectedCloneSource,
    selectedCustomAgent,
    selectedTemplate,
  ])

  useEffect(() => {
    if (availableGroupIds.length === 0) {
      if (groupId) setGroupId('')
      return
    }
    if (groupId && availableGroupIds.includes(groupId)) {
      return
    }

    const overrideGroup = Object.keys(groupPromptOverrides)
      .map((group) => group.trim().toUpperCase())
      .find((group) => availableGroupIds.includes(group))
    if (overrideGroup) {
      setGroupId(overrideGroup)
      return
    }

    const loggedInGroup = loggedInGroupIds.find((group) => availableGroupIds.includes(group))
    if (loggedInGroup) {
      setGroupId(loggedInGroup)
      return
    }

    if (groupId) {
      setGroupId('')
    }
  }, [availableGroupIds, groupId, groupPromptOverrides, loggedInGroupIds])

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
      setDebouncedGroupPromptOverrides(groupPromptOverrides)
    }, 450)
    return () => {
      window.clearTimeout(timeout)
    }
  }, [groupPromptOverrides])

  useEffect(() => {
    if (!onContextChange) return
    const contextTemplateId = selectedCustomAgent?.template_source
      || (gettingStartedMode === 'template' ? parentAgentId : undefined)
    const contextTemplateName = contextTemplateId
      ? (selectedTemplate?.name || parentAgent?.agent_name)
      : undefined
    const normalizedSelectedGroupOverrides = selectedCustomAgent?.group_prompt_overrides || {}
    const draftIsDirty = selectedCustomAgent
      ? customPrompt !== selectedCustomAgent.custom_prompt
        || !areStringRecordsEqual(groupPromptOverrides, normalizedSelectedGroupOverrides)
        || includeGroupRules !== selectedCustomAgent.include_group_rules
        || !areStringArraysEqual(selectedAllowedGroupIds, selectedCustomAgent.allowed_group_ids || [])
        || selectedModelId !== selectedCustomAgent.model_id
        || (selectedModelReasoning || '') !== (selectedCustomAgent.model_reasoning || '')
        || !areStringArraysEqual(selectedToolIds, selectedCustomAgent.tool_ids || [])
        || (outputSchemaKey || '') !== (selectedCustomAgent.output_schema_key || '')
      : Boolean(customPrompt.trim())
    onContextChange({
      template_source: contextTemplateId || undefined,
      template_name: contextTemplateName,
      custom_agent_id: selectedCustomAgent?.agent_id,
      custom_agent_name: selectedCustomAgent?.name,
      include_group_rules: includeGroupRules,
      selected_group_id: selectedGroupId || undefined,
      prompt_draft: debouncedPromptDraft,
      selected_group_prompt_draft: selectedGroupPromptForContext,
      draft_is_dirty: draftIsDirty,
      custom_agent_updated_at: selectedCustomAgent?.updated_at,
      group_prompt_override_count: Object.keys(debouncedGroupPromptOverrides).length,
      has_group_prompt_overrides: Object.keys(debouncedGroupPromptOverrides).length > 0,
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
    selectedCustomAgent,
    includeGroupRules,
    selectedGroupId,
    customPrompt,
    groupPromptOverrides,
    debouncedPromptDraft,
    selectedGroupPromptForContext,
    debouncedGroupPromptOverrides,
    selectedToolIds,
    selectedAllowedGroupIds,
    selectedModelId,
    selectedModelReasoning,
    outputSchemaKey,
  ])

  useEffect(() => {
    if (!incomingPromptUpdate) return
    if (!workshopOptionsLoaded) return
    if (loading || customAgentsLoadingRef.current) return
    if (gettingStartedMode === 'template' && !parentAgentId && !selectedCustomAgentId) return
    if (gettingStartedMode === 'clone' && !selectedCustomAgentId && !selectedCloneSource && !cloneSourceAgentId) return
    if (
      appliedPromptUpdateId.current === incomingPromptUpdate.request_id
      && appliedPromptUpdateHydrationVersion.current === hydrationVersion
    ) {
      return
    }
    appliedPromptUpdateId.current = incomingPromptUpdate.request_id
    appliedPromptUpdateHydrationVersion.current = hydrationVersion

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

    const targetPrompt = incomingPromptUpdate.target_prompt === 'group' ? 'group' : 'main'
    if (targetPrompt === 'group') {
      const targetGroupId = (incomingPromptUpdate.target_group_id || selectedGroupId || '').trim().toUpperCase()
      if (!targetGroupId) {
        setError('Cannot apply group prompt update because no group is selected.')
        return
      }
      if (availableGroupIds.length > 0 && !availableGroupIds.includes(targetGroupId)) {
        setError(`Cannot apply group prompt update: ${targetGroupId} is not available for this template.`)
        return
      }

      setGroupId(targetGroupId)
      setGroupPromptOverrides((prev) => ({ ...prev, [targetGroupId]: incomingPromptUpdate.prompt }))
      setDebouncedGroupPromptOverrides((prev) => ({ ...prev, [targetGroupId]: incomingPromptUpdate.prompt }))
      setError(null)
      setStatus(
        incomingPromptUpdate.summary?.trim()
          ? `Applied AI Chat group update (${targetGroupId}): ${incomingPromptUpdate.summary.trim()}`
          : `Applied AI Chat prompt update to ${targetGroupId} group draft`
      )
      return
    }

    setCustomPrompt(incomingPromptUpdate.prompt)
    setDebouncedPromptDraft(incomingPromptUpdate.prompt)
    setError(null)
    setStatus(
      incomingPromptUpdate.summary?.trim()
        ? `Applied AI Chat update: ${incomingPromptUpdate.summary.trim()}`
        : 'Applied AI Chat prompt update to the draft'
    )
  }, [
    incomingPromptUpdate,
    availableGroupIds,
    selectedGroupId,
    workshopOptionsLoaded,
    gettingStartedMode,
    parentAgentId,
    selectedCustomAgentId,
    selectedCloneSource,
    cloneSourceAgentId,
    loading,
    hydrationVersion,
  ])

  useEffect(() => {
    if (!dirty.any) return
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [dirty.any])

  const selectCustomAgent = useCallback((agentId: string) => {
    setSelectedCustomAgentId(agentId)
    setSaveState('idle')
    setLastSavedAt(null)
    const agent = customAgents.find((candidate) => candidate.id === agentId)
    if (agent) setStatus(`Opened "${agent.name}"`)
  }, [customAgents])

  const handleNew = useCallback(() => {
    if (selectedCustomAgent) {
      setCloneSourceAgentId(selectedCustomAgent.id)
    }
    setSelectedCustomAgentId('')
    setSelectedVisibility('private')
    setSelectedAllowedGroupIds([])
    setSaveState('idle')
    setLastSavedAt(null)
    setStatus('Creating a new custom agent draft')
  }, [selectedCustomAgent])

  const startDraft = useCallback((mode: GettingStartedMode) => {
    if (selectedCustomAgent) {
      setCloneSourceAgentId(selectedCustomAgent.id)
    }
    setGettingStartedMode(mode)
    setSelectedCustomAgentId('')
    setSaveState('idle')
    setLastSavedAt(null)
  }, [selectedCustomAgent])

  const reloadAfterSave = useCallback(async (keepId?: string) => {
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
  }, [getTemplateAlignedAgentId, refreshAgentMetadata])

  const handleSave = useCallback(async (options?: SaveOptions, selfExclusionConfirmed = false) => {
    const forceCreate = options?.forceCreate ?? false
    const nameToSave = (options?.nameOverride ?? name).trim()
    const notes = (options?.notes ?? '').trim()

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
    if (!customPrompt.trim() && !parentAgentId) {
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
    const excludesCurrentUserGroups = selectedAllowedGroupIds.length > 0
      && currentUserGroupIds.length > 0
      && !currentUserGroupIds.some((candidate) => selectedAllowedGroupIds.includes(candidate))
    if (excludesCurrentUserGroups && !selfExclusionConfirmed) {
      setSelfExclusionPrompt(options ?? {})
      return
    }

    setSaving(true)
    setSaveState('saving')
    setError(null)
    setStatus(null)

    try {
      const shouldCreate = forceCreate || !selectedCustomAgentId
      if (!shouldCreate && selectedCustomAgentId) {
        let updated = await updateCustomAgent(selectedCustomAgentId, {
          name: nameToSave,
          description: description.trim() || undefined,
          custom_prompt: customPrompt,
          group_prompt_overrides: groupPromptOverrides,
          include_group_rules: includeGroupRules,
          model_id: selectedModelId,
          model_reasoning: selectedModelReasoning || undefined,
          tool_ids: selectedToolIds,
          output_schema_key: outputSchemaKey || undefined,
          icon: icon || undefined,
          notes: notes || undefined,
          allowed_group_ids: selectedAllowedGroupIds,
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
          group_prompt_overrides: groupPromptOverrides,
          include_group_rules: includeGroupRules,
          model_id: selectedModelId,
          model_reasoning: selectedModelReasoning || undefined,
          tool_ids: selectedToolIds,
          output_schema_key: outputSchemaKey || undefined,
          icon: icon || undefined,
          allowed_group_ids: selectedAllowedGroupIds,
        })
        if (selectedVisibility === 'project') {
          created = await setCustomAgentVisibility(created.agent_id, 'project')
        }
        await reloadAfterSave(created.id)
        setStatus(forceCreate ? `Saved as "${created.name}"` : `Created "${created.name}"`)
      }
      setSaveState('saved')
      setLastSavedAt(Date.now())
    } catch (err) {
      setSaveState('failed')
      setError(err instanceof Error ? err.message : 'Failed to save custom agent')
    } finally {
      setSaving(false)
    }
  }, [
    cloneSourceAgentId,
    currentUserGroupIds,
    customPrompt,
    description,
    gettingStartedMode,
    groupPromptOverrides,
    icon,
    includeGroupRules,
    name,
    outputSchemaKey,
    parentAgentId,
    reloadAfterSave,
    selectedAllowedGroupIds,
    selectedCloneSource?.template_source,
    selectedCustomAgent?.template_source,
    selectedCustomAgent?.tool_ids,
    selectedCustomAgentId,
    selectedModelId,
    selectedModelReasoning,
    selectedToolIds,
    selectedVisibility,
  ])

  const confirmSelfExclusion = useCallback(() => {
    const options = selfExclusionPrompt
    setSelfExclusionPrompt(null)
    void handleSave(options ?? undefined, true)
  }, [handleSave, selfExclusionPrompt])

  const cancelSelfExclusion = useCallback(() => {
    setSelfExclusionPrompt(null)
  }, [])

  const handleDeleteById = useCallback(async (agent: CustomAgent) => {
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
  }, [customAgents, handleNew, reloadAfterSave, selectedCustomAgentId])

  const handleRevert = useCallback(async (version: number) => {
    if (!selectedCustomAgentId) return
    setSaving(true)
    setError(null)
    try {
      const reverted = await revertCustomAgentVersion(selectedCustomAgentId, version, undefined)
      await reloadAfterSave(reverted.id)
      setStatus(`Reverted to version ${version}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revert version')
    } finally {
      setSaving(false)
    }
  }, [reloadAfterSave, selectedCustomAgentId])

  const handleSelectedGroupPromptChange = useCallback((value: string) => {
    if (!selectedGroupId) return
    setGroupPromptOverrides((prev) => {
      const next = { ...prev }
      if (value === selectedGroupBasePrompt || (!value.trim() && !selectedGroupBasePrompt.trim())) {
        delete next[selectedGroupId]
      } else {
        next[selectedGroupId] = value
      }
      return next
    })
  }, [selectedGroupBasePrompt, selectedGroupId])

  const handleResetSelectedGroupPrompt = useCallback(() => {
    if (!selectedGroupId) return
    setGroupPromptOverrides((prev) => {
      if (!Object.prototype.hasOwnProperty.call(prev, selectedGroupId)) return prev
      const next = { ...prev }
      delete next[selectedGroupId]
      return next
    })
  }, [selectedGroupId])

  const resetCustomPromptToTemplate = useCallback(() => {
    setCustomPrompt(parentBasePrompt)
  }, [parentBasePrompt])

  const removeTool = useCallback((toolKey: string) => {
    setSelectedToolIds((prev) => prev.filter((existing) => existing !== toolKey))
  }, [])

  const applyToolSelection = useCallback((toolIds: string[]) => {
    setSelectedToolIds(toolIds.filter((toolKey) => {
      const policy = toolPolicyByKey.get(toolKey)
      return !policy || policy.allow_attach
    }))
  }, [toolPolicyByKey])

  const submitToolIdea = useCallback(async (
    title: string,
    ideaDescription: string,
    conversation: ToolIdeaConversationEntry[]
  ): Promise<boolean> => {
    if (!title.trim()) {
      setError('Please enter a tool request title')
      return false
    }
    if (!ideaDescription.trim()) {
      setError('Please enter a tool request description')
      return false
    }

    setToolIdeaSubmitting(true)
    setError(null)
    try {
      const ideationTranscript = conversation
        .filter((entry) => Boolean(entry.content && entry.content.trim()))
        .slice(-30)
      const created = await submitToolIdeaRequest({
        title: title.trim(),
        description: ideaDescription.trim(),
        opus_conversation: ideationTranscript,
      })
      const response = await listToolIdeaRequests()
      setToolIdeaRequests(response.tool_ideas)
      setStatus(`Submitted tool request "${created.title}"`)
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit tool request')
      return false
    } finally {
      setToolIdeaSubmitting(false)
    }
  }, [])

  const handleModelChange = useCallback((modelId: string) => {
    setSelectedModelId(modelId)
    setSelectedModelReasoning(resolveReasoningSelection(modelOptions, modelId))
  }, [modelOptions])

  return {
    modelOptions,
    toolLibrary,
    templateOptions,
    groupOptions,
    loading,

    gettingStartedMode,
    parentAgentId,
    setParentAgentId,
    parentAgent,
    selectedTemplate,
    templateMissing,
    customAgents,
    selectedCustomAgentId,
    selectedCustomAgent,
    selectCustomAgent,
    cloneSourceAgentId,
    setCloneSourceAgentId,
    selectedCloneSource,
    isNewDraft,

    name,
    setName,
    description,
    setDescription,
    icon,
    setIcon,
    iconOptions,
    customPrompt,
    setCustomPrompt,
    resetCustomPromptToTemplate,
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
    removeTool,
    applyToolSelection,

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
    saveState,
    lastSavedAt,
    status,
    error,
    setError,
    setStatus,
    dirty,
    canSave,

    handleNew,
    startDraft,
    handleSave,
    handleDeleteById,
    handleRevert,
    selfExclusionPrompt,
    confirmSelfExclusion,
    cancelSelfExclusion,
  }
}
