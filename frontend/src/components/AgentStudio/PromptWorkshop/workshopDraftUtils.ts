import type { CustomAgent, ModelOption, PromptInfo, ToolIdeaRequest } from '@/types/promptExplorer'

export const FALLBACK_ICON_OPTIONS = ['🔧', '🧬', '📄', '🔍', '🧪', '📊', '🧠', '⚙️', '✨', '📝', '📚', '🧩']
export const DEFAULT_AGENT_ICON = '🔧'
export const ALL_GROUPS_VALUE = '__all_groups__'

export type GettingStartedMode = 'template' | 'scratch' | 'clone'
export type WorkshopSection = 'setup' | 'prompt' | 'tools' | 'versions'
export type WorkshopVisibility = 'private' | 'project'
export type SaveState = 'idle' | 'saving' | 'saved' | 'failed'

/** Every field a curator can edit. Compared against the last saved snapshot for dirty tracking. */
export interface DraftFields {
  name: string
  description: string
  customPrompt: string
  groupPromptOverrides: Record<string, string>
  includeGroupRules: boolean
  visibility: WorkshopVisibility
  allowedGroupIds: string[]
  modelId: string
  modelReasoning: string
  toolIds: string[]
  outputSchemaKey: string
  icon: string
}

export interface DraftDirtyState {
  setup: boolean
  prompt: boolean
  tools: boolean
  /** Group ids whose override text differs from the snapshot. */
  groups: string[]
  any: boolean
}

export function areStringRecordsEqual(left: Record<string, string>, right: Record<string, string>): boolean {
  const leftKeys = Object.keys(left).sort()
  const rightKeys = Object.keys(right).sort()
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index] && left[key] === right[key])
}

export function areStringArraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false
  const sortedLeft = [...left].sort()
  const sortedRight = [...right].sort()
  return sortedLeft.every((value, index) => value === sortedRight[index])
}

export function changedOverrideGroups(
  current: Record<string, string>,
  saved: Record<string, string>
): string[] {
  const keys = new Set([...Object.keys(current), ...Object.keys(saved)])
  return Array.from(keys)
    .filter((key) => current[key] !== saved[key])
    .sort()
}

export function computeDirtyState(current: DraftFields, saved: DraftFields | null): DraftDirtyState {
  if (!saved) {
    return { setup: false, prompt: false, tools: false, groups: [], any: false }
  }
  const setup = current.name !== saved.name
    || current.description !== saved.description
    || current.icon !== saved.icon
    || current.visibility !== saved.visibility
    || !areStringArraysEqual(current.allowedGroupIds, saved.allowedGroupIds)
    || current.modelId !== saved.modelId
    || current.modelReasoning !== saved.modelReasoning
  const groups = changedOverrideGroups(current.groupPromptOverrides, saved.groupPromptOverrides)
  const prompt = current.customPrompt !== saved.customPrompt
    || current.includeGroupRules !== saved.includeGroupRules
  const tools = !areStringArraysEqual(current.toolIds, saved.toolIds)
  return {
    setup,
    prompt,
    tools,
    groups,
    any: setup || prompt || tools || groups.length > 0,
  }
}

/** Human labels for the Save dialog's "Changed since" line. */
export function describeChangedSections(dirty: DraftDirtyState): string[] {
  const sections: string[] = []
  if (dirty.setup) sections.push('Setup')
  if (dirty.prompt) sections.push('Your prompt')
  dirty.groups.forEach((groupId) => sections.push(`${groupId} instructions`))
  if (dirty.tools) sections.push('Tools')
  return sections
}

export function formatCharCount(text: string): string {
  const length = text.length
  if (length < 1000) return String(length)
  const thousands = length / 1000
  return `${thousands >= 10 ? Math.round(thousands) : thousands.toFixed(1)}k`
}

export function formatRelativeTime(timestamp: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000))
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return `${days} d ago`
}

export function formatVersionDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function toolIdeaStatusLabel(request: ToolIdeaRequest): string {
  switch (request.status) {
    case 'submitted':
      return 'New'
    case 'reviewed':
      return 'Reviewed'
    case 'in_progress':
      return 'In progress'
    case 'completed':
      return request.resulting_tool_key ? `Shipped ${request.resulting_tool_key}` : 'Shipped'
    case 'declined':
      return 'Declined'
  }
}

export function toolIdeaStatusColor(
  status: ToolIdeaRequest['status']
): 'default' | 'info' | 'warning' | 'success' | 'error' {
  if (status === 'submitted' || status === 'reviewed') return 'info'
  if (status === 'in_progress') return 'warning'
  if (status === 'completed') return 'success'
  if (status === 'declined') return 'error'
  return 'default'
}

export function normalizeReasoningValue(value?: string | null): string {
  return (value || '').trim().toLowerCase()
}

export function formatReasoningLabel(value: string): string {
  const normalized = normalizeReasoningValue(value)
  if (!normalized) return value
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

export function resolveModelSelection(
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

export function resolveReasoningSelection(
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

export function resolveUserGroupIds(userGroups: string[] | undefined, availableGroupIds: string[]): string[] {
  if (!userGroups || userGroups.length === 0 || availableGroupIds.length === 0) return []
  const available = new Set(availableGroupIds.map((group) => group.toUpperCase()))
  const resolved: string[] = []
  for (const rawGroup of userGroups) {
    const normalized = rawGroup.trim().toUpperCase()
    if (!normalized) continue
    const direct = available.has(normalized) ? normalized : ''
    const inferred = direct || availableGroupIds.find((groupId) => {
      const loweredGroup = rawGroup.trim().toLowerCase()
      return loweredGroup === groupId.toLowerCase() || loweredGroup.includes(groupId.toLowerCase())
    })?.toUpperCase() || ''
    if (inferred && !resolved.includes(inferred)) {
      resolved.push(inferred)
    }
  }
  return resolved
}

export function joinPromptLayers(agent: PromptInfo | undefined, kind: 'core_static' | 'core_generated'): string {
  return (agent?.prompt_layers || [])
    .filter((layer) => layer.kind === kind)
    .map((layer) => layer.content)
    .join('\n\n')
}

export function resolveParentBasePrompt(agent: PromptInfo | undefined): string {
  const fromLayers = (agent?.prompt_layers || [])
    .filter((layer) => layer.kind === 'base_prompt')
    .map((layer) => layer.content)
    .join('\n\n')
  return fromLayers || agent?.base_prompt || ''
}

export function cloneDraftName(source: CustomAgent): string {
  return source.name.endsWith(' (Copy)') ? source.name : `${source.name} (Copy)`
}

/** Short, stable identifier shown next to a tool request ("request 3f9a"). */
export function shortRequestId(id: string): string {
  return id.replace(/-/g, '').slice(0, 6)
}

export function buildDiscussDraftMessage(targetName: string, targetId: string, selectedGroupId: string): string {
  const groupPart = selectedGroupId ? `Selected Group: ${selectedGroupId}` : 'Selected Group: none'
  return `Discuss my Agent Workshop draft for "${targetName}".\n\nPlease refresh the current draft and inspect current prompt/tool schemas before giving authoritative advice.\n\nPlease help with:\n1. Prompt quality and clarity issues\n2. Risky or ambiguous instructions\n3. Concrete edits to improve behavior\n4. Suggested flow-based validation tests\n5. Whether any PDF evidence instructions preserve search_document, read_chunk span IDs, and record_evidence(span_ids)\n\nAgent ID: ${targetId}\n${groupPart}\n\n[Request ID: ${Date.now()}]`
}

export function buildDiscussPromptMessage(targetName: string, targetId: string, selectedGroupId: string): string {
  const groupPart = selectedGroupId ? `Selected Group: ${selectedGroupId}` : 'Selected Group: none'
  return `Help me improve the SYSTEM PROMPT for "${targetName}".\n\nPlease refresh the current draft and inspect current prompt/tool schemas before proposing edits.\n\nPlease:\n1. Identify unclear, conflicting, or risky instructions.\n2. Propose concrete edits focused on behavior and extraction quality.\n3. Explain why each suggested edit helps.\n4. Keep changes minimal unless a full rewrite is truly needed.\n5. Preserve span-backed PDF evidence guidance when document tools are attached: search_document for candidates, read_chunk for span IDs, and record_evidence(span_ids) for retained evidence.\n\nAgent ID: ${targetId}\n${groupPart}\n\n[Request ID: ${Date.now()}]`
}

export function buildModelAdviceMessage(
  targetName: string,
  modelOptions: ModelOption[],
  selectedModelId: string,
  selectedModelReasoning: string,
  selectedToolIds: string[]
): string {
  const modelLines = modelOptions
    // modelOptions already comes from GET /models, which the backend filters to
    // curator-visible models (config/models.yaml curator_visible), so no extra filter here.
    .map((model) => {
      const reasoning = model.reasoning_options.length > 0
        ? `Reasoning: ${model.reasoning_options.join(', ')} (default: ${model.default_reasoning || 'none'})`
        : 'Reasoning: n/a'
      return `- ${model.name} [${model.model_id}] via ${model.provider}\n  Guidance: ${model.guidance || model.description || 'n/a'}\n  ${reasoning}`
    }).join('\n')

  return `Help me choose the best model settings for my Agent Workshop draft.\n\nAgent draft: ${targetName}\nCurrent model: ${selectedModelId || 'none'}\nCurrent reasoning: ${selectedModelReasoning || 'none'}\nAttached tools: ${selectedToolIds.length > 0 ? selectedToolIds.join(', ') : 'none'}\n\nAvailable models (authoritative configured choices):\n${modelLines}\n\nUse only the available models and their configured guidance above. Do not rely on historical model names or unlisted variants.\n\nPlease:\n1. Ask 1-3 focused questions to understand my use case\n2. Recommend a model and (if applicable) reasoning level\n3. Explain tradeoffs in plain curator-friendly language\n4. Give one backup model choice\n\n[Request ID: ${Date.now()}]`
}

export function buildToolRequestMessage(targetName: string, targetId: string, selectedToolIds: string[]): string {
  const attachedTools = selectedToolIds.length > 0 ? selectedToolIds.join(', ') : 'none'
  return `I need help designing a NEW tool request for Agent Workshop.\n\nContext:\n- Agent draft: ${targetName}\n- Agent ID: ${targetId}\n- Attached tools: ${attachedTools}\n\nPlease guide me with focused questions and help me produce:\n1. A concise request title\n2. Clear problem statement\n3. Required inputs\n4. Expected output format\n5. One concrete usage example\n\nWhen we finish, provide a final polished request that I can submit to developers.\n\n[Request ID: ${Date.now()}]`
}
