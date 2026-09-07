import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type {
  AgentTemplate,
  AgentWorkshopContext,
  CustomAgent,
  GroupOption,
  ModelOption,
  PromptCatalog,
  PromptInfo,
  ToolIdeaConversationEntry,
  ToolIdeaRequest,
  ToolLibraryItem,
} from '@/types/promptExplorer'
import {
  createCustomAgent,
  getWorkshopSavedReference,
  getAgentExecutionRevision,
  deleteCustomAgent,
  fetchAgentTemplates,
  fetchModelOptions,
  fetchToolLibrary,
  listAgentExecutionRevisions,
  listCustomAgents,
  listToolIdeaRequests,
  restoreAgentExecutionRevision,
  submitToolIdeaRequest,
  updateCustomAgent,
} from '@/services/agentStudioService'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import type { AgentExecutionRevision } from '@/types/agentExecution'
import { getGenericProfile, getGenericProfileRevision, validateGenericProfile, type GenericProfileDetail, type ProfileMappingDiagnostic } from '@/services/genericProfileService'
import { emptyOutputDraft, hydrateProfileOutput, outputDraftFromContract, outputDraftSavePayload, profileValidationIssues, type WorkshopOutputDraft } from './workshopOutputDraft'
import { profileCandidateToDraft } from './profileEditorModel'
import { useAuth } from '@/contexts/AuthContext'
import { logger } from '@/services/logger'
import { flushSync } from 'react-dom'
import { fingerprintWorkshopDraft, canonicalAuthoringJson, workshopDraftKey } from '../authoringContext'
import { validateWorkshopDraft } from '@/services/agentStudioService'
import type { WorkshopAuthoringProposal } from '@/types/promptExplorer'
import type { WorkshopContinuationOrigin, WorkshopSavedHandoff } from '@/types/promptExplorer'
import type { FlowProposalApplyResult } from '../FlowBuilder/types'

import {
  DEFAULT_AGENT_ICON,
  FALLBACK_ICON_OPTIONS,
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
  continuationOrigin?: WorkshopContinuationOrigin
  onSavedHandoff?: (handoff: WorkshopSavedHandoff) => void
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
  setChatCloneSource: (agent: CustomAgent) => void
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
  outputDraft: WorkshopOutputDraft
  setOutputDraft: (value: WorkshopOutputDraft) => void
  outputLoading: boolean
  outputLoadError: string | null
  profileCanEdit: boolean
  savedExecutionRevision: AgentExecutionRevision | null
  selectOutputProfile: (profile: GenericProfileDetail) => void
  retryOutputLoad: () => void
  profileIssues: ProfileMappingDiagnostic[]
  profileValidating: boolean
  validateOutputProfile: () => Promise<boolean>

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
  versions: AgentExecutionRevision[]
  versionsLoading: boolean
  versionsError: string | null
  hasMoreVersions: boolean
  loadMoreVersions: () => void
  retryVersions: () => void

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
  /** The selected draft and any saved output structure have finished loading. */
  isHydrated: boolean
  /** Copy the complete current editable value synchronously for an AI Chat turn. */
  captureAuthoringContext: () => AgentWorkshopContext
  applyAuthoringProposal: (proposal: WorkshopAuthoringProposal) => Promise<FlowProposalApplyResult>
  undoAuthoringProposal: () => Promise<void>
  canUndoAuthoringProposal: boolean
  authoringBusy: boolean

  // Actions
  handleNew: () => void
  /** Detach from the open agent (if any) and start a fresh draft in the given mode. */
  startDraft: (mode: GettingStartedMode, customExtractionTemplateId?: string) => void
  handleSave: (options?: SaveOptions, selfExclusionConfirmed?: boolean) => Promise<void>
  handleDeleteById: (agent: CustomAgent) => Promise<void>
  handleRevert: (version: AgentExecutionRevision) => Promise<void>
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
  continuationOrigin,
  onSavedHandoff,
}: UseWorkshopDraftArgs): WorkshopDraft {
  const { agents: agentMetadata, refresh: refreshAgentMetadata } = useAgentMetadata()
  const { user: authUser } = useAuth()

  const [parentAgentId, setParentAgentId] = useState('')
  const [gettingStartedMode, setGettingStartedMode] = useState<GettingStartedMode>(
    initialParentAgentId ? 'template' : 'scratch',
  )
  const [customAgents, setCustomAgents] = useState<CustomAgent[]>([])
  const [draftResetGeneration, setDraftResetGeneration] = useState(0)
  const [customExtractionTemplateId, setCustomExtractionTemplateId] = useState<string>()
  const [chatCloneSource, setChatCloneSource] = useState<CustomAgent>()
  const chatCloneSourceRef = useRef(chatCloneSource)
  chatCloneSourceRef.current = chatCloneSource
  const [selectedCustomAgentId, setSelectedCustomAgentId] = useState<string>('')
  const [cloneSourceAgentId, setCloneSourceAgentId] = useState<string>('')
  const [versions, setVersions] = useState<AgentExecutionRevision[]>([])
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [versionsError, setVersionsError] = useState<string | null>(null)
  const [nextVersionCursor, setNextVersionCursor] = useState<number | null>(null)
  const [versionCursor, setVersionCursor] = useState<number | undefined>()
  const [versionRetry, setVersionRetry] = useState(0)
  const versionHistoryKey = useRef('')
  const [loading, setLoading] = useState(false)
  const [workshopOptionsLoaded, setWorkshopOptionsLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')
  const [groupPromptOverrides, setGroupPromptOverrides] = useState<Record<string, string>>({})
  const [includeGroupRules, setIncludeGroupRules] = useState(true)
  const [selectedVisibility, setSelectedVisibility] = useState<WorkshopVisibility>('private')
  const [selectedAllowedGroupIds, setSelectedAllowedGroupIds] = useState<string[]>([])
  const [selectedModelId, setSelectedModelId] = useState('')
  const [selectedModelReasoning, setSelectedModelReasoning] = useState('')
  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([])
  const [outputDraft, setOutputDraft] = useState<WorkshopOutputDraft>(() => emptyOutputDraft())
  const [outputLoading, setOutputLoading] = useState(false)
  const [outputLoadError, setOutputLoadError] = useState<string | null>(null)
  const [outputLoadAttempt, setOutputLoadAttempt] = useState(0)
  const [profileSource, setProfileSource] = useState<GenericProfileDetail | null>(null)
  const [savedExecutionRevision, setSavedExecutionRevision] = useState<AgentExecutionRevision | null>(null)
  const profileCanEdit = Boolean(profileSource?.can_edit && profileSource.revision.id === outputDraft.profilePin?.profile_revision_id)
  const outputSchemaKey = outputDraft.mode === 'domain' ? outputDraft.schemaKey : ''
  const [profileValidation, setProfileValidation] = useState<{
    draft: WorkshopOutputDraft; pending: boolean; issues: ProfileMappingDiagnostic[]
  } | null>(null)
  const profileValidationRequest = useRef(0)
  const validateOutputProfile = useCallback(async () => {
    if (outputDraft.mode !== 'profile_bound_generic' || !outputDraft.profileContract) return true
    const request = ++profileValidationRequest.current
    setProfileValidation({ draft: outputDraft, pending: true, issues: [] })
    try {
      await validateGenericProfile(outputDraft.profileContract)
      if (request === profileValidationRequest.current) setProfileValidation({ draft: outputDraft, pending: false, issues: [] })
      return true
    } catch (error) {
      if (request === profileValidationRequest.current) setProfileValidation({ draft: outputDraft, pending: false, issues: profileValidationIssues(error) })
      return false
    }
  }, [outputDraft])
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
    (chatCloneSource?.id === cloneSourceAgentId ? chatCloneSource : undefined)
      || customAgents.find((agent) => agent.id === cloneSourceAgentId)
  )
  const hydrationKey = JSON.stringify([selectedCustomAgentId, selectedCustomAgent?.execution_revision_id,
    gettingStartedMode, parentAgentId, cloneSourceAgentId, selectedCloneSource?.execution_revision_id,
    draftResetGeneration, customExtractionTemplateId])
  const [hydratedKey, setHydratedKey] = useState<string | null>(null)

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
    if (Object.prototype.hasOwnProperty.call(groupPromptOverrides, selectedGroupId)) {
      return groupPromptOverrides[selectedGroupId]
    }
    return groupRuleSourceAgent?.group_rules[selectedGroupId]?.content
  }, [groupPromptOverrides, groupRuleSourceAgent, selectedGroupId])

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
    outputDraft,
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
    outputDraft,
    icon,
  ])

  const dirty = useMemo(() => computeDirtyState(currentFields, savedSnapshot), [currentFields, savedSnapshot])
  const isNewDraft = !selectedCustomAgent
  const canSave = !saving && !outputLoading && !outputLoadError && (isNewDraft || dirty.any)

  const applyDraft = useCallback((fields: DraftFields, preserveBaseline = false) => {
    setName(fields.name)
    setDescription(fields.description)
    setCustomPrompt(fields.customPrompt)
    setGroupPromptOverrides(fields.groupPromptOverrides)
    setIncludeGroupRules(fields.includeGroupRules)
    setSelectedAllowedGroupIds(fields.allowedGroupIds)
    setSelectedVisibility(fields.visibility)
    setSelectedModelId(fields.modelId)
    setSelectedModelReasoning(fields.modelReasoning)
    setSelectedToolIds(fields.toolIds)
    setOutputDraft(fields.outputDraft)
    setIcon(fields.icon)
    if (!preserveBaseline) {
      setSavedSnapshot(fields)
    }
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
          if (stillExists || chatCloneSourceRef.current?.id === prev) return prev
          return templateAlignedAgentId || response.custom_agents[0].id
        })
      } else {
        setSelectedCustomAgentId('')
        setCloneSourceAgentId((prev) => chatCloneSourceRef.current?.id === prev ? prev : '')
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
    const key = `${selectedCustomAgentId ?? ''}:${selectedCustomAgent?.execution_revision_id ?? ''}`
    if (versionHistoryKey.current !== key) {
      versionHistoryKey.current = key
      setVersions([])
      setNextVersionCursor(null)
      if (versionCursor !== undefined) {
        setVersionCursor(undefined)
        return
      }
    }
    let cancelled = false
    async function loadVersions() {
      if (!selectedCustomAgentId) {
        setVersions([])
        setNextVersionCursor(null)
        setVersionsLoading(false)
        setVersionsError(null)
        return
      }
      setVersionsLoading(true)
      setVersionsError(null)
      if (versionCursor === undefined) setVersions([])
      try {
        const loaded = await listAgentExecutionRevisions(selectedCustomAgentId, versionCursor)
        if (cancelled) return
        setVersions((previous) => versionCursor === undefined ? loaded.revisions : [
          ...previous, ...loaded.revisions.filter((revision) => !previous.some((item) => item.id === revision.id)),
        ])
        setNextVersionCursor(loaded.next_before_revision)
      } catch (err) {
        if (!cancelled) setVersionsError(err instanceof Error ? err.message : 'Could not load saved configurations')
      } finally {
        if (!cancelled) setVersionsLoading(false)
      }
    }
    void loadVersions()
    return () => { cancelled = true }
  }, [selectedCustomAgentId, selectedCustomAgent?.execution_revision_id, versionCursor, versionRetry])

  useEffect(() => {
    // Hydrate only once model and template options are known, so the draft is
    // built once and a later options arrival cannot overwrite curator edits.
    if (!workshopOptionsLoaded) return
    const hydrateDraft = (fields: DraftFields) => {
      applyDraft(fields)
      setHydratedKey(hydrationKey)
    }
    const hydrateSaved = (fields: Omit<DraftFields, 'outputDraft'>, source: CustomAgent) => {
      let canceled = false
      setOutputLoading(true)
      setOutputLoadError(null)
      void (async () => {
        try {
          if (!source.execution_revision_id) throw new Error('This agent has no executable revision to load.')
          const revision = await getAgentExecutionRevision(source.id, source.execution_revision_id)
          if (revision.id !== source.execution_revision_id || revision.agent_id !== source.id) throw new Error('The saved configuration identity changed. Reload the agent.')
          let output = outputDraftFromContract(revision.snapshot.output_contract)
          let sourceProfile: GenericProfileDetail | null = null
          if (output.profilePin) {
            const [detail, savedProfile] = await Promise.all([
              getGenericProfile(output.profilePin.profile_id),
              getGenericProfileRevision(output.profilePin.profile_id, output.profilePin.revision),
            ])
            output = hydrateProfileOutput(output, savedProfile)
            sourceProfile = { ...detail, revision: savedProfile }
          }
          if (!canceled) {
            setProfileSource(sourceProfile)
            setSavedExecutionRevision(revision)
            hydrateDraft({ ...fields, outputDraft: output })
          }
        } catch (error) {
          if (!canceled) setOutputLoadError(error instanceof Error ? error.message : 'Could not load the saved Output Structure.')
        } finally {
          if (!canceled) setOutputLoading(false)
        }
      })()
      return () => { canceled = true }
    }
    if (!selectedCustomAgent) {
      if (gettingStartedMode === 'clone' && selectedCloneSource) {
        const cloneModelId = resolveModelSelection(modelOptions, defaultModelId, selectedCloneSource.model_id)
        if (selectedCloneSource.template_source) setParentAgentId(selectedCloneSource.template_source)
        return hydrateSaved({
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
          icon: selectedCloneSource.icon || DEFAULT_AGENT_ICON,
        }, selectedCloneSource)
      }

      if (gettingStartedMode === 'scratch') {
        setOutputLoading(false)
        setOutputLoadError(null)
        hydrateDraft({
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
          outputDraft: emptyOutputDraft(),
          icon: DEFAULT_AGENT_ICON,
        })
        return
      }

      const templateModelId = resolveModelSelection(modelOptions, defaultModelId, selectedTemplate?.model_id)
      setOutputLoading(false)
      setOutputLoadError(null)
      hydrateDraft({
        name: parentAgent ? `${parentAgent.agent_name} (Custom)` : '',
        description: '',
        customPrompt: '',
        groupPromptOverrides: {},
        includeGroupRules: true,
        allowedGroupIds: selectedTemplate?.allowed_group_ids || [],
        visibility: 'private',
        modelId: templateModelId,
        modelReasoning: resolveReasoningSelection(modelOptions, templateModelId),
        toolIds: selectedTemplate?.tool_ids || [],
        outputDraft: customExtractionTemplateId === parentAgentId
          || selectedTemplate?.output_contract?.output_mode === 'unprofiled_generic'
          ? emptyOutputDraft('profile_bound_generic')
          : selectedTemplate?.output_contract
          ? outputDraftFromContract(selectedTemplate.output_contract)
          : emptyOutputDraft(),
        icon: DEFAULT_AGENT_ICON,
      })
      return
    }

    const customModelId = resolveModelSelection(modelOptions, defaultModelId, selectedCustomAgent.model_id)
    if (selectedCustomAgent.template_source) setParentAgentId(selectedCustomAgent.template_source)
    return hydrateSaved({
      name: selectedCustomAgent.name,
      description: selectedCustomAgent.description || '',
      customPrompt: selectedCustomAgent.custom_prompt || '',
      groupPromptOverrides: selectedCustomAgent.group_prompt_overrides || {},
      includeGroupRules: selectedCustomAgent.include_group_rules,
      allowedGroupIds: selectedCustomAgent.allowed_group_ids || [],
      visibility: selectedCustomAgent.visibility === 'project' ? 'project' : 'private',
      modelId: customModelId,
      modelReasoning: resolveReasoningSelection(modelOptions, customModelId, selectedCustomAgent.model_reasoning),
      toolIds: selectedCustomAgent.tool_ids || [],
      icon: selectedCustomAgent.icon || DEFAULT_AGENT_ICON,
    }, selectedCustomAgent)
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
    outputLoadAttempt,
    draftResetGeneration,
    hydrationKey,
    customExtractionTemplateId,
    parentAgentId,
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

  const captureAuthoringContext = useCallback((): AgentWorkshopContext => {
    const contextTemplateId = selectedCustomAgent?.template_source
      || (gettingStartedMode === 'template' ? parentAgentId
        : gettingStartedMode === 'clone' ? selectedCloneSource?.template_source : undefined)
    const contextTemplateName = contextTemplateId
      ? (selectedTemplate?.name || parentAgent?.agent_name)
      : undefined
    return {
      getting_started_mode: gettingStartedMode,
      template_source: contextTemplateId || undefined,
      clone_source_agent_id: gettingStartedMode === 'clone' && !selectedCustomAgent
        ? selectedCloneSource?.agent_id : undefined,
      clone_source_updated_at: gettingStartedMode === 'clone' && !selectedCustomAgent
        ? selectedCloneSource?.updated_at : undefined,
      template_name: contextTemplateName,
      custom_agent_id: selectedCustomAgent?.agent_id,
      custom_agent_name: selectedCustomAgent?.name,
      draft_name: name,
      draft_description: description,
      draft_icon: icon,
      draft_visibility: selectedVisibility,
      draft_allowed_group_ids: [...selectedAllowedGroupIds],
      inherited_allowed_group_ids: [...inheritedAllowedGroupIds],
      include_group_rules: includeGroupRules,
      selected_group_id: selectedGroupId || undefined,
      prompt_draft: customPrompt,
      selected_group_prompt_draft: selectedGroupPromptForContext,
      group_prompt_overrides: { ...groupPromptOverrides },
      draft_is_dirty: dirty.any,
      custom_agent_updated_at: selectedCustomAgent?.updated_at,
      group_prompt_override_count: Object.keys(groupPromptOverrides).length,
      has_group_prompt_overrides: Object.keys(groupPromptOverrides).length > 0,
      draft_tool_ids: [...selectedToolIds],
      draft_model_id: selectedModelId || undefined,
      draft_model_reasoning: selectedModelReasoning || undefined,
      draft_output_schema_key: outputSchemaKey || undefined,
      draft_output: structuredClone(outputDraft),
    }
  }, [
    customPrompt,
    description,
    dirty.any,
    gettingStartedMode,
    groupPromptOverrides,
    icon,
    includeGroupRules,
    inheritedAllowedGroupIds,
    name,
    outputSchemaKey,
    outputDraft,
    parentAgent?.agent_name,
    parentAgentId,
    selectedAllowedGroupIds,
    selectedCustomAgent,
    selectedCloneSource?.agent_id,
    selectedCloneSource?.updated_at,
    selectedCloneSource?.template_source,
    selectedGroupId,
    selectedGroupPromptForContext,
    selectedModelId,
    selectedModelReasoning,
    selectedTemplate?.name,
    selectedToolIds,
    selectedVisibility,
  ])

  useEffect(() => {
    if (!onContextChange) return
    onContextChange(captureAuthoringContext())
  }, [captureAuthoringContext, onContextChange])

  const liveAuthoringRef = useRef({ captureAuthoringContext, currentFields, saving, loading })
  liveAuthoringRef.current = { captureAuthoringContext, currentFields, saving, loading }
  const applyingAuthoringRef = useRef(false)
  const authoringMountedRef = useRef(true)
  useEffect(() => {
    authoringMountedRef.current = true
    return () => { authoringMountedRef.current = false }
  }, [])
  const [authoringBusy, setAuthoringBusy] = useState(false)
  const [authoringUndo, setAuthoringUndo] = useState<{
    fields: DraftFields
    applied: string
    profileSource: GenericProfileDetail | null
  } | null>(null)

  const applyAuthoringProposal = useCallback(async (proposal: WorkshopAuthoringProposal): Promise<FlowProposalApplyResult> => {
    if (applyingAuthoringRef.current || liveAuthoringRef.current.saving || liveAuthoringRef.current.loading) {
      return { applied: false, message: 'Wait for the current Workshop operation to finish.' }
    }
    applyingAuthoringRef.current = true
    setAuthoringBusy(true)
    try {
      const before = liveAuthoringRef.current.captureAuthoringContext()
      const beforeKey = workshopDraftKey(before)
      if (await fingerprintWorkshopDraft(before) !== proposal.base_draft_fingerprint) {
        return { applied: false, message: 'The Workshop changed. Generate a fresh proposal.' }
      }
      const candidate = structuredClone(proposal.candidate)
      if (!candidate.draft_output) return { applied: false, message: 'The proposal is missing the complete output draft. Generate a fresh proposal.' }
      if (await fingerprintWorkshopDraft(candidate) !== proposal.candidate_draft_fingerprint
        || candidate.custom_agent_id !== before.custom_agent_id
        || candidate.custom_agent_updated_at !== before.custom_agent_updated_at
        || candidate.template_source !== before.template_source
        || candidate.clone_source_agent_id !== before.clone_source_agent_id
        || candidate.clone_source_updated_at !== before.clone_source_updated_at
        || candidate.getting_started_mode !== before.getting_started_mode
        || canonicalAuthoringJson(candidate.inherited_allowed_group_ids ?? [])
          !== canonicalAuthoringJson(before.inherited_allowed_group_ids ?? [])) {
        return { applied: false, message: 'The proposal identity or fingerprint is invalid.' }
      }
      candidate.draft_fingerprint = proposal.candidate_draft_fingerprint
      const validation = await validateWorkshopDraft(candidate, 'pre_apply')
      if (!validation.valid) {
        return { applied: false, message: validation.findings.map((item) => item.message).join(' ') }
      }
      let nextProfileSource: GenericProfileDetail | null = null
      const selectedPin = candidate.draft_output.profilePin
      if (selectedPin) {
        const [detail, revision] = await Promise.all([
          getGenericProfile(selectedPin.profile_id),
          getGenericProfileRevision(selectedPin.profile_id, selectedPin.revision),
        ])
        // Validate exact source identity without replacing the reviewed edits.
        hydrateProfileOutput(candidate.draft_output, revision)
        nextProfileSource = { ...detail, revision }
      }
      if (!authoringMountedRef.current
        || workshopDraftKey(liveAuthoringRef.current.captureAuthoringContext()) !== beforeKey
        || liveAuthoringRef.current.saving) {
        return { applied: false, message: 'The Workshop changed during validation. Generate a fresh proposal.' }
      }
      const previousFields = structuredClone(liveAuthoringRef.current.currentFields)
      const nextFields: DraftFields = {
        name: candidate.draft_name ?? '', description: candidate.draft_description ?? '',
        customPrompt: candidate.prompt_draft ?? '', groupPromptOverrides: candidate.group_prompt_overrides ?? {},
        includeGroupRules: candidate.include_group_rules ?? false,
        visibility: candidate.draft_visibility ?? 'private',
        allowedGroupIds: candidate.draft_allowed_group_ids ?? [],
        modelId: candidate.draft_model_id ?? '', modelReasoning: candidate.draft_model_reasoning ?? '',
        toolIds: candidate.draft_tool_ids ?? [],
        outputDraft: { ...structuredClone(candidate.draft_output),
          profileContract: candidate.draft_output.profileContract
            ? profileCandidateToDraft(candidate.draft_output.profileContract) : null },
        icon: candidate.draft_icon ?? '',
      }
      flushSync(() => applyDraft(nextFields, true))
      const actual = liveAuthoringRef.current.captureAuthoringContext()
      const appliedKey = workshopDraftKey(actual)
      try {
        actual.draft_fingerprint = await fingerprintWorkshopDraft(actual)
        if (actual.draft_fingerprint !== proposal.candidate_draft_fingerprint) {
          throw new Error('The editor did not reproduce the reviewed candidate.')
        }
        const afterValidation = await validateWorkshopDraft(actual, 'post_apply')
        if (!authoringMountedRef.current) {
          return { applied: false, message: 'The Workshop was closed. Generate a fresh proposal.' }
        }
        if (!afterValidation.valid) {
          if (workshopDraftKey(liveAuthoringRef.current.captureAuthoringContext()) === appliedKey) {
            flushSync(() => applyDraft(previousFields, true))
          }
          return { applied: false, message: afterValidation.findings.map((item) => item.message).join(' ') || 'The applied draft failed validation.' }
        }
        if (workshopDraftKey(liveAuthoringRef.current.captureAuthoringContext()) !== appliedKey) {
          setAuthoringUndo(null)
          return { applied: false, message: 'The Workshop changed after Apply; your latest edits were preserved.' }
        }
      } catch {
        logger.error('Workshop post-apply validation failed', new Error('Workshop validation failed'), {
          component: 'PromptWorkshop', action: 'proposal_post_apply',
        })
        if (workshopDraftKey(liveAuthoringRef.current.captureAuthoringContext()) === appliedKey) {
          flushSync(() => applyDraft(previousFields, true))
        }
        return { applied: false, message: 'Workshop validation failed. The proposal was not accepted.' }
      }
      setAuthoringUndo({ fields: previousFields, applied: appliedKey, profileSource })
      setProfileSource(nextProfileSource)
      logger.info('Workshop proposal applied', { component: 'PromptWorkshop', action: 'proposal_apply' })
      setStatus('Applied AI changes to the draft. Review and Save when ready.')
      return { applied: true, message: 'Applied to the Workshop draft. Save remains separate.' }
    } catch {
      logger.error('Workshop proposal validation failed', new Error('Workshop validation failed'), {
        component: 'PromptWorkshop', action: 'proposal_pre_apply',
      })
      return { applied: false, message: 'Unable to validate the Workshop proposal. Please try again.' }
    } finally {
      applyingAuthoringRef.current = false
      setAuthoringBusy(false)
    }
  }, [applyDraft, profileSource])

  const canUndoAuthoringProposal = Boolean(authoringUndo
    && authoringUndo.applied === workshopDraftKey(captureAuthoringContext()))
  useEffect(() => {
    if (authoringUndo && !canUndoAuthoringProposal) setAuthoringUndo(null)
  }, [authoringUndo, canUndoAuthoringProposal])
  const undoAuthoringProposal = useCallback(async () => {
    if (!authoringUndo || applyingAuthoringRef.current || liveAuthoringRef.current.saving
      || authoringUndo.applied !== workshopDraftKey(liveAuthoringRef.current.captureAuthoringContext())) return
    applyDraft(authoringUndo.fields, true)
    setProfileSource(authoringUndo.profileSource)
    setAuthoringUndo(null)
    logger.info('Workshop proposal undone', { component: 'PromptWorkshop', action: 'proposal_undo' })
    setStatus('Undid the last AI change.')
  }, [applyDraft, authoringUndo])


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
    setDraftResetGeneration((generation) => generation + 1)
    setSelectedCustomAgentId(agentId)
    setSaveState('idle')
    setLastSavedAt(null)
    const agent = customAgents.find((candidate) => candidate.id === agentId)
    if (agent) setStatus(`Opened "${agent.name}"`)
  }, [customAgents])

  const handleNew = useCallback(() => {
    setCustomExtractionTemplateId(undefined)
    setDraftResetGeneration((generation) => generation + 1)
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

  const selectParentAgent = useCallback((agentId: string) => {
    setCustomExtractionTemplateId(undefined)
    setParentAgentId(agentId)
  }, [])

  const startDraft = useCallback((mode: GettingStartedMode, extractionTemplateId?: string) => {
    setCustomExtractionTemplateId(extractionTemplateId)
    if (extractionTemplateId) setParentAgentId(extractionTemplateId)
    setDraftResetGeneration((generation) => generation + 1)
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
    const saved = response.custom_agents.find((agent) => agent.id === keepId && agent.is_active)
    if (keepId && !saved) {
      throw new Error('The saved agent is not available in the refreshed catalog.')
    }
    if (!saved) return undefined
    const reference = await getWorkshopSavedReference(saved.id)
    return reference.agent_id === saved.agent_id ? saved : undefined
  }, [getTemplateAlignedAgentId, refreshAgentMetadata])

  const handleSave = useCallback(async (options?: SaveOptions, selfExclusionConfirmed = false) => {
    if (applyingAuthoringRef.current) return
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

    let persistedAgent: CustomAgent | undefined
    try {
      if (outputLoading || outputLoadError) throw new Error(outputLoadError || 'Wait for the saved Output Structure to load.')
      if (!await validateOutputProfile()) throw new Error('Check the highlighted Output Structure fields before saving.')
      const sourceContract = profileSource?.revision.id === outputDraft.profilePin?.profile_revision_id
        ? profileSource?.revision.contract ?? null : savedSnapshot?.outputDraft.profileContract ?? null
      const outputPayload = outputDraftSavePayload(outputDraft, sourceContract, !forceCreate && Boolean(selectedCustomAgentId) && profileCanEdit)
      const shouldCreate = forceCreate || !selectedCustomAgentId
      let savedIdentity: string | undefined
      if (!shouldCreate && selectedCustomAgentId) {
        const updated = await updateCustomAgent(selectedCustomAgentId, {
          visibility: selectedVisibility,
          expected_updated_at: selectedCustomAgent?.updated_at,
          name: nameToSave,
          description,
          custom_prompt: customPrompt,
          group_prompt_overrides: groupPromptOverrides,
          include_group_rules: includeGroupRules,
          model_id: selectedModelId,
          model_reasoning: selectedModelReasoning,
          tool_ids: selectedToolIds,
          ...outputPayload,
          expected_revision_id: selectedCustomAgent?.execution_revision_id ?? undefined,
          icon: icon || undefined,
          notes: notes || undefined,
          allowed_group_ids: selectedAllowedGroupIds,
        })
        persistedAgent = updated
        const refreshed = await reloadAfterSave(updated.id)
        if (refreshed?.agent_id === updated.agent_id) savedIdentity = updated.agent_id
        setStatus(`Updated "${updated.name}"`)
      } else {
        const templateSource = selectedCustomAgent?.template_source
          || (gettingStartedMode === 'template'
            ? parentAgentId
            : (gettingStartedMode === 'clone' ? selectedCloneSource?.template_source : undefined))
        const cloneSource = selectedCustomAgent
          || (gettingStartedMode === 'clone' ? selectedCloneSource : undefined)
        const created = await createCustomAgent({
          visibility: selectedVisibility,
          template_source: templateSource || undefined,
          clone_source_agent_id: cloneSource?.agent_id,
          clone_source_updated_at: cloneSource?.updated_at,
          name: nameToSave,
          description,
          custom_prompt: customPrompt,
          group_prompt_overrides: groupPromptOverrides,
          include_group_rules: includeGroupRules,
          model_id: selectedModelId,
          model_reasoning: selectedModelReasoning,
          tool_ids: selectedToolIds,
          ...outputPayload,
          icon: icon || undefined,
          allowed_group_ids: selectedAllowedGroupIds,
        })
        persistedAgent = created
        const refreshed = await reloadAfterSave(created.id)
        if (refreshed?.agent_id === created.agent_id) savedIdentity = created.agent_id
        setStatus(forceCreate ? `Saved as "${created.name}"` : `Created "${created.name}"`)
      }
      setSaveState('saved')
      setLastSavedAt(Date.now())
      const origin = continuationOrigin?.node_id && continuationOrigin.agent_id !== savedIdentity
        ? { flow_id: continuationOrigin.flow_id, flow_draft_fingerprint: continuationOrigin.flow_draft_fingerprint }
        : continuationOrigin
      onSavedHandoff?.({
        // Only a fresh authorized catalog can emit an actionable saved identity.
        status: savedIdentity ? 'ready' : 'catalog_unavailable',
        saved_agent_id: savedIdentity,
        saved_custom_agent_id: savedIdentity ? persistedAgent.id : undefined,
        saved_agent_revision_id: savedIdentity ? persistedAgent.execution_revision_id || undefined : undefined,
        saved_agent_name: persistedAgent.name,
        origin,
      })
    } catch (err) {
      if (persistedAgent) {
        logger.error('Workshop catalog handoff failed', new Error('Workshop catalog handoff failed'), {
          component: 'PromptWorkshop', action: 'catalog_handoff',
        })
        const saved = persistedAgent
        setCustomAgents((agents) => [...agents.filter((agent) => agent.id !== saved.id), saved])
        setSelectedCustomAgentId(saved.id)
        setSaveState('saved')
        setLastSavedAt(Date.now())
        setError('The agent was saved, but catalog refresh did not finish. Refresh before continuing.')
        onSavedHandoff?.({ status: 'catalog_unavailable', origin: continuationOrigin })
      } else {
        setSaveState('failed')
        setError(err instanceof Error ? err.message : 'Failed to save custom agent')
      }
    } finally {
      setSaving(false)
    }
  }, [
    cloneSourceAgentId,
    continuationOrigin,
    onSavedHandoff,
    currentUserGroupIds,
    customPrompt,
    description,
    gettingStartedMode,
    groupPromptOverrides,
    icon,
    includeGroupRules,
    name,
    outputDraft,
    outputLoading,
    outputLoadError,
    validateOutputProfile,
    profileCanEdit,
    profileSource,
    savedSnapshot,
    parentAgentId,
    reloadAfterSave,
    selectedAllowedGroupIds,
    selectedCloneSource?.template_source,
    selectedCloneSource?.agent_id,
    selectedCloneSource?.updated_at,
    selectedCustomAgent?.template_source,
    selectedCustomAgent?.tool_ids,
    selectedCustomAgent?.updated_at,
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

  const handleRevert = useCallback(async (version: AgentExecutionRevision) => {
    if (!selectedCustomAgentId || !selectedCustomAgent?.execution_revision_id) return
    setSaving(true)
    setError(null)
    try {
      const reverted = await restoreAgentExecutionRevision(selectedCustomAgentId, version.id, selectedCustomAgent.execution_revision_id)
      await reloadAfterSave(reverted.id)
      setStatus(`Restored configuration ${version.revision} as a new version`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to restore configuration')
    } finally {
      setSaving(false)
    }
  }, [reloadAfterSave, selectedCustomAgentId, selectedCustomAgent?.execution_revision_id])

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
    setCustomPrompt('')
  }, [])

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
    setParentAgentId: selectParentAgent,
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
    setChatCloneSource,
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
    outputDraft,
    setOutputDraft,
    profileIssues: profileValidation?.draft === outputDraft ? profileValidation.issues : [],
    profileValidating: profileValidation?.draft === outputDraft && profileValidation.pending,
    validateOutputProfile,
    outputLoading,
    outputLoadError,
    retryOutputLoad: () => setOutputLoadAttempt((attempt) => attempt + 1),
    profileCanEdit,
    savedExecutionRevision,

    selectOutputProfile: (profile) => {
      const revision = profile.revision
      setProfileSource(profile)
      setOutputDraft(hydrateProfileOutput(outputDraftFromContract({
        output_state: 'structured_extraction', output_mode: 'profile_bound_generic',
        generic_profile_ref: { profile_id: revision.profile_id, profile_revision_id: revision.id, revision: revision.revision, fingerprint: revision.fingerprint },
      }), revision))
    },
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
    versionsLoading,
    versionsError,
    hasMoreVersions: nextVersionCursor !== null,
    loadMoreVersions: () => { if (nextVersionCursor !== null && !versionsLoading) setVersionCursor(nextVersionCursor) },
    retryVersions: () => { if (!versionsLoading) setVersionRetry((previous) => previous + 1) },

    saving,
    saveState,
    lastSavedAt,
    status,
    error,
    setError,
    setStatus,
    dirty,
    canSave,
    captureAuthoringContext,
    isHydrated: hydratedKey === hydrationKey && !loading && !outputLoading && !outputLoadError,
    applyAuthoringProposal,
    undoAuthoringProposal,
    canUndoAuthoringProposal,
    authoringBusy,

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
