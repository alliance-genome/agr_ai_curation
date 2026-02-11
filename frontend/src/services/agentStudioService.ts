/**
 * API service for Agent Studio feature.
 */

import type {
  PromptCatalog,
  PromptPreviewResponse,
  CustomAgent,
  CustomAgentVersion,
  TraceContext,
  ChatMessage,
  ChatContext,
  OpusChatEvent,
  SuggestionSubmission,
  SuggestionResponse,
  ToolInfo,
} from '@/types/promptExplorer'

const BASE_URL = '/api/agent-studio'

// =============================================================================
// Agent Metadata Types
// =============================================================================

/**
 * Metadata for a single agent
 */
export interface AgentMetadata {
  name: string
  icon: string
  category: string
  subcategory?: string
  supervisor_tool?: string
}

/**
 * Response from /registry/metadata endpoint
 */
export interface RegistryMetadataResponse {
  agents: Record<string, AgentMetadata>
}

// =============================================================================
// Agent Metadata API
// =============================================================================

/**
 * Fetch agent metadata for frontend display
 * Returns icons, names, and categories for all agents
 */
export async function fetchRegistryMetadata(): Promise<RegistryMetadataResponse> {
  const response = await fetch(`${BASE_URL}/registry/metadata`)
  if (!response.ok) {
    throw new Error(`Failed to fetch registry metadata: ${response.status}`)
  }
  return response.json()
}

// =============================================================================
// Prompt Catalog API
// =============================================================================

/**
 * Fetch the prompt catalog (all agents organized by category)
 */
export async function fetchPromptCatalog(): Promise<PromptCatalog> {
  const response = await fetch(`${BASE_URL}/catalog`)
  if (!response.ok) {
    throw new Error(`Failed to fetch catalog: ${response.status}`)
  }
  const data = await response.json()
  return data.catalog
}

/**
 * Force refresh the prompt catalog
 */
export async function refreshPromptCatalog(): Promise<PromptCatalog> {
  const response = await fetch(`${BASE_URL}/catalog/refresh`, {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error(`Failed to refresh catalog: ${response.status}`)
  }
  const data = await response.json()
  return data.catalog
}

/**
 * Get combined prompt (base + MOD rules injected)
 */
export async function fetchCombinedPrompt(
  agentId: string,
  modId: string
): Promise<string> {
  const response = await fetch(`${BASE_URL}/catalog/combined`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId, mod_id: modId }),
  })
  if (!response.ok) {
    throw new Error(`Failed to fetch combined prompt: ${response.status}`)
  }
  const data = await response.json()
  return data.combined_prompt
}

/**
 * Get resolved prompt preview for system/custom agents.
 */
export async function fetchPromptPreview(
  agentId: string,
  modId?: string
): Promise<PromptPreviewResponse> {
  const params = new URLSearchParams()
  if (modId) {
    params.set('mod_id', modId)
  }
  const query = params.toString() ? `?${params.toString()}` : ''
  const response = await fetch(`${BASE_URL}/prompt-preview/${encodeURIComponent(agentId)}${query}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch prompt preview: ${response.status}`)
  }
  return response.json()
}

// =============================================================================
// Custom Agent API (Prompt Workshop)
// =============================================================================

export interface CreateCustomAgentRequest {
  parent_agent_id: string
  name: string
  custom_prompt?: string
  description?: string
  icon?: string
  include_mod_rules?: boolean
}

export interface UpdateCustomAgentRequest {
  name?: string
  custom_prompt?: string
  description?: string
  icon?: string
  include_mod_rules?: boolean
  notes?: string
  rebase_parent_hash?: boolean
}

export interface CustomAgentTestRequest {
  input: string
  mod_id?: string
  document_id?: string
  session_id?: string
}

export interface ActiveChatDocumentResponse {
  active: boolean
  document?: {
    id: string
    filename?: string
  }
  message?: string
}

export interface ListCustomAgentsResponse {
  custom_agents: CustomAgent[]
  total: number
}

export async function listCustomAgents(parentAgentId?: string): Promise<ListCustomAgentsResponse> {
  const params = new URLSearchParams()
  if (parentAgentId) {
    params.set('parent_agent_id', parentAgentId)
  }
  const query = params.toString() ? `?${params.toString()}` : ''
  const response = await fetch(`${BASE_URL}/custom-agents${query}`)
  if (!response.ok) {
    throw new Error(`Failed to list custom agents: ${response.status}`)
  }
  return response.json()
}

export async function createCustomAgent(
  request: CreateCustomAgentRequest
): Promise<CustomAgent> {
  const response = await fetch(`${BASE_URL}/custom-agents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    throw new Error(`Failed to create custom agent: ${response.status}`)
  }
  return response.json()
}

export async function getCustomAgent(customAgentId: string): Promise<CustomAgent> {
  const response = await fetch(`${BASE_URL}/custom-agents/${encodeURIComponent(customAgentId)}`)
  if (!response.ok) {
    throw new Error(`Failed to get custom agent: ${response.status}`)
  }
  return response.json()
}

export async function updateCustomAgent(
  customAgentId: string,
  request: UpdateCustomAgentRequest
): Promise<CustomAgent> {
  const response = await fetch(`${BASE_URL}/custom-agents/${encodeURIComponent(customAgentId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    throw new Error(`Failed to update custom agent: ${response.status}`)
  }
  return response.json()
}

export async function deleteCustomAgent(customAgentId: string): Promise<void> {
  const response = await fetch(`${BASE_URL}/custom-agents/${encodeURIComponent(customAgentId)}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(`Failed to delete custom agent: ${response.status}`)
  }
}

export async function listCustomAgentVersions(customAgentId: string): Promise<CustomAgentVersion[]> {
  const response = await fetch(`${BASE_URL}/custom-agents/${encodeURIComponent(customAgentId)}/versions`)
  if (!response.ok) {
    throw new Error(`Failed to list custom agent versions: ${response.status}`)
  }
  return response.json()
}

export async function revertCustomAgentVersion(
  customAgentId: string,
  version: number,
  notes?: string
): Promise<CustomAgent> {
  const response = await fetch(
    `${BASE_URL}/custom-agents/${encodeURIComponent(customAgentId)}/revert/${version}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes }),
    }
  )
  if (!response.ok) {
    throw new Error(`Failed to revert custom agent version: ${response.status}`)
  }
  return response.json()
}

export async function fetchActiveChatDocument(): Promise<ActiveChatDocumentResponse> {
  const response = await fetch('/api/chat/document')
  if (!response.ok) {
    throw new Error(`Failed to fetch active document: ${response.status}`)
  }
  return response.json()
}

export async function* streamCustomAgentTest(
  customAgentId: string,
  request: CustomAgentTestRequest
): AsyncGenerator<Record<string, unknown>> {
  const response = await fetch(
    `${BASE_URL}/custom-agents/${encodeURIComponent(customAgentId)}/test`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }
  )

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `Failed to run custom agent test: ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('No response body from custom agent test')
  }

  yield* streamSseReader(reader)
}

export async function* streamAgentTest(
  agentId: string,
  request: CustomAgentTestRequest
): AsyncGenerator<Record<string, unknown>> {
  const response = await fetch(
    `${BASE_URL}/test-agent/${encodeURIComponent(agentId)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }
  )

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `Failed to run agent test: ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('No response body from agent test')
  }

  yield* streamSseReader(reader)
}

async function* streamSseReader(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<Record<string, unknown>> {

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const event = JSON.parse(line.slice(6)) as Record<string, unknown>
          yield event
        } catch {
          // Ignore malformed partial events.
        }
      }
    }

    if (buffer.startsWith('data: ')) {
      try {
        const event = JSON.parse(buffer.slice(6)) as Record<string, unknown>
        yield event
      } catch {
        // Ignore incomplete final event.
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/**
 * Fetch trace context for display
 */
export async function fetchTraceContext(
  traceId: string
): Promise<TraceContext | null> {
  const response = await fetch(`${BASE_URL}/trace/${traceId}/context`)
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(`Failed to fetch trace context: ${response.status}`)
  }
  const data = await response.json()
  return data.context
}

/**
 * Stream chat with Opus 4.5
 * Returns an async generator that yields SSE events
 *
 * Uses effort="medium" on the backend for optimal quality/cost balance.
 *
 * @param messages - Chat messages to send
 * @param context - Optional context (selected agent, MOD, trace, etc.)
 */
export async function* streamOpusChat(
  messages: ChatMessage[],
  context?: ChatContext
): AsyncGenerator<OpusChatEvent> {
  const response = await fetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, context }),
  })

  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('No response body')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6)) as OpusChatEvent
            yield event
          } catch {
            // Silently skip malformed SSE events - they may be incomplete
          }
        }
      }
    }

    // Process any remaining buffer
    if (buffer.startsWith('data: ')) {
      try {
        const event = JSON.parse(buffer.slice(6)) as OpusChatEvent
        yield event
      } catch {
        // Ignore incomplete final event
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/**
 * Submit a manual prompt suggestion
 */
export async function submitSuggestion(
  suggestion: SuggestionSubmission
): Promise<SuggestionResponse> {
  const response = await fetch(`${BASE_URL}/suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(suggestion),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `Failed to submit suggestion: ${response.status}`)
  }

  return response.json()
}

// =============================================================================
// Tool Details API
// =============================================================================

/**
 * Fetch details for a specific tool.
 * Optionally pass agentId to get agent-specific context for multi-method tools.
 *
 * @param toolId - The tool identifier (e.g., "agr_curation_query", "search_document")
 * @param agentId - Optional agent ID to get agent-specific method context
 */
export async function fetchToolDetails(
  toolId: string,
  agentId?: string
): Promise<ToolInfo> {
  const params = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
  const response = await fetch(`${BASE_URL}/tools/${encodeURIComponent(toolId)}${params}`)

  if (!response.ok) {
    if (response.status === 404) {
      throw new Error(`Tool "${toolId}" not found`)
    }
    throw new Error(`Failed to fetch tool details: ${response.status}`)
  }

  const data = await response.json()
  return data.tool
}

/**
 * Fetch all available tools.
 * Returns a record of tool_id -> ToolInfo
 */
export async function fetchAllTools(): Promise<Record<string, ToolInfo>> {
  const response = await fetch(`${BASE_URL}/tools`)

  if (!response.ok) {
    throw new Error(`Failed to fetch tools: ${response.status}`)
  }

  const data = await response.json()
  return data.tools
}

// =============================================================================
// Flow Management API
// =============================================================================

import type {
  FlowResponse,
  FlowListResponse,
  CreateFlowRequest,
  UpdateFlowRequest,
} from '@/components/AgentStudio/FlowBuilder/types'

const FLOWS_URL = '/api/flows'

/**
 * List all flows for the current user
 */
export async function listFlows(page = 1, pageSize = 20): Promise<FlowListResponse> {
  const response = await fetch(`${FLOWS_URL}?page=${page}&page_size=${pageSize}`)
  if (!response.ok) {
    throw new Error(`Failed to list flows: ${response.status}`)
  }
  return response.json()
}

/**
 * Get a single flow by ID
 */
export async function getFlow(flowId: string): Promise<FlowResponse> {
  const response = await fetch(`${FLOWS_URL}/${flowId}`)
  if (!response.ok) {
    throw new Error(`Failed to get flow: ${response.status}`)
  }
  return response.json()
}

/**
 * Helper to extract error message from API response
 */
function extractErrorMessage(error: { detail?: string | Array<{ msg?: string; message?: string }> }, fallback: string): string {
  if (typeof error.detail === 'string') {
    return error.detail
  }
  if (Array.isArray(error.detail)) {
    // Pydantic validation errors are arrays
    return error.detail.map((e) => e.msg || e.message || String(e)).join('; ')
  }
  return fallback
}

/**
 * Create a new flow
 */
export async function createFlow(flow: CreateFlowRequest): Promise<FlowResponse> {
  const response = await fetch(FLOWS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(flow),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(extractErrorMessage(error, `Failed to create flow: ${response.status}`))
  }
  return response.json()
}

/**
 * Update an existing flow
 */
export async function updateFlow(
  flowId: string,
  updates: UpdateFlowRequest
): Promise<FlowResponse> {
  const response = await fetch(`${FLOWS_URL}/${flowId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(extractErrorMessage(error, `Failed to update flow: ${response.status}`))
  }
  return response.json()
}

/**
 * Delete a flow
 */
export async function deleteFlow(flowId: string): Promise<void> {
  const response = await fetch(`${FLOWS_URL}/${flowId}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(`Failed to delete flow: ${response.status}`)
  }
}
