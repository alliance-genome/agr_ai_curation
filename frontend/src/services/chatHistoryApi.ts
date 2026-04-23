import { readCurationApiError } from '@/features/curation/services/api'
import { normalizeChatHistoryValue } from '@/lib/chatHistoryNormalization'
import type { FileInfo } from '@/components/Chat/FileDownloadCard'
import type { EvidenceRecord } from '@/features/curation/types'
import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

export type PersistedChatHistoryKind = 'assistant_chat' | 'agent_studio'
export type ChatHistoryListKind = PersistedChatHistoryKind | 'all'

export const ASSISTANT_CHAT_HISTORY_KIND: PersistedChatHistoryKind = 'assistant_chat'

export interface ChatHistoryActiveDocument {
  id: string
  filename?: string | null
  chunk_count?: number | null
  vector_count?: number | null
  metadata?: Record<string, unknown> | null
}

export interface ChatHistorySessionSummary {
  session_id: string
  chat_kind: PersistedChatHistoryKind
  title?: string | null
  active_document_id?: string | null
  created_at: string
  updated_at: string
  last_message_at?: string | null
  recent_activity_at: string
}

export interface ChatHistoryMessage {
  message_id: string
  session_id: string
  chat_kind: PersistedChatHistoryKind
  turn_id?: string | null
  role: string
  message_type: string
  content: string
  payload_json?: Record<string, unknown> | unknown[] | null
  trace_id?: string | null
  created_at: string
}

export type RestorableChatMessageRole = 'user' | 'assistant' | 'flow'

export interface RestorableChatMessage {
  id?: string
  role: RestorableChatMessageRole
  content: string
  timestamp: string
  traceIds?: string[]
  turnId?: string
  type?: 'text' | 'file_download'
  fileData?: FileInfo
  flowStepEvidence?: FlowStepEvidenceDetails
  evidenceRecords?: EvidenceRecord[]
  evidenceCurationSupported?: boolean | null
  evidenceCurationAdapterKey?: string | null
}

export interface ChatHistoryListRequest {
  chatKind: ChatHistoryListKind
  limit?: number
  cursor?: string | null
  query?: string | null
  documentId?: string | null
}

export interface ChatHistoryListResponse {
  chat_kind: ChatHistoryListKind
  total_sessions: number
  limit: number
  query?: string | null
  document_id?: string | null
  next_cursor?: string | null
  sessions: ChatHistorySessionSummary[]
}

export interface ChatHistoryDetailRequest {
  sessionId: string
  messageLimit?: number
  messageCursor?: string | null
}

export interface ChatHistoryDetailResponse {
  session: ChatHistorySessionSummary
  active_document?: ChatHistoryActiveDocument | null
  messages: ChatHistoryMessage[]
  message_limit: number
  next_message_cursor?: string | null
}

export interface RenameChatSessionRequest {
  sessionId: string
  title: string
}

export interface RenameChatSessionResponse {
  session: ChatHistorySessionSummary
}

export interface DeleteChatSessionRequest {
  sessionId: string
}

export interface BulkDeleteChatSessionsRequest {
  sessionIds: string[]
}

export interface BulkDeleteChatSessionsResponse {
  requested_count: number
  deleted_count: number
  deleted_session_ids: string[]
}

interface ChatHistoryFetchOptions {
  expectJson?: boolean
}

export interface EvidenceCurationSupport {
  supported: boolean
  adapterKey: string | null
}

interface RestorableChatMessageOptions {
  onUnknownRole?: 'skip' | 'throw'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function isEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.entity === 'string' &&
    typeof value.verified_quote === 'string' &&
    typeof value.page === 'number' &&
    Number.isFinite(value.page) &&
    typeof value.section === 'string' &&
    typeof value.chunk_id === 'string'
  )
}

function extractEvidenceRecords(value: unknown): EvidenceRecord[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.filter(isEvidenceRecord)
}

function extractPayloadRecords(
  payload: Record<string, unknown> | null,
): Array<Record<string, unknown>> {
  if (!payload) {
    return []
  }

  const records = [payload]
  const nestedValues = [payload.details, payload.data, payload.file, payload.fileData]

  nestedValues.forEach((value) => {
    if (isRecord(value)) {
      records.push(value)
    }
  })

  return records
}

function extractFileData(payload: Record<string, unknown> | null): FileInfo | null {
  for (const candidate of extractPayloadRecords(payload)) {
    const fileId = readString(candidate.file_id)
    const filename = readString(candidate.filename)
    const format = readString(candidate.format)
    const downloadUrl = readString(candidate.download_url)

    if (!fileId || !filename || !format || !downloadUrl) {
      continue
    }

    return {
      file_id: fileId,
      filename,
      format,
      download_url: downloadUrl,
      size_bytes: readNumber(candidate.size_bytes) ?? undefined,
      mime_type: readString(candidate.mime_type) ?? undefined,
      created_at: readString(candidate.created_at) ?? undefined,
    }
  }

  return null
}

function extractFlowStepEvidence(payload: Record<string, unknown> | null): FlowStepEvidenceDetails | null {
  for (const candidate of extractPayloadRecords(payload)) {
    const flowId = readString(candidate.flow_id)
    const flowRunId = readString(candidate.flow_run_id)
    const step = readNumber(candidate.step)
    const evidenceCount = readNumber(candidate.evidence_count)
    const totalEvidenceRecords = readNumber(candidate.total_evidence_records)

    if (!flowId || !flowRunId || step == null || evidenceCount == null || totalEvidenceRecords == null) {
      continue
    }

    return {
      flow_id: flowId,
      flow_name: readString(candidate.flow_name),
      flow_run_id: flowRunId,
      step,
      tool_name: readString(candidate.tool_name),
      agent_id: readString(candidate.agent_id),
      agent_name: readString(candidate.agent_name),
      evidence_records: extractEvidenceRecords(candidate.evidence_records),
      evidence_count: evidenceCount,
      total_evidence_records: totalEvidenceRecords,
    }
  }

  return null
}

export function extractEvidenceCurationSupport(value: unknown): EvidenceCurationSupport | null {
  if (!isRecord(value) || typeof value.curation_supported !== 'boolean') {
    return null
  }

  const adapterKey = typeof value.curation_adapter_key === 'string'
    && value.curation_adapter_key.trim().length > 0
    ? value.curation_adapter_key.trim()
    : null

  return {
    supported: value.curation_supported,
    adapterKey,
  }
}

function toRestorableRole(
  message: ChatHistoryMessage,
  options: RestorableChatMessageOptions,
): RestorableChatMessageRole | null {
  if (message.message_type === 'file_download') {
    return 'assistant'
  }

  if (message.message_type === 'flow_step_evidence') {
    return 'flow'
  }

  if (message.role === 'user' || message.role === 'assistant' || message.role === 'flow') {
    return message.role
  }

  if (options.onUnknownRole === 'throw') {
    throw new Error(`Unknown transcript message role: ${message.role}`)
  }

  return null
}

function buildRestorableChatMessageBase(
  message: ChatHistoryMessage,
  role: RestorableChatMessageRole,
): RestorableChatMessage {
  return {
    id: message.message_id,
    role,
    content: message.content,
    timestamp: message.created_at,
    traceIds: message.trace_id ? [message.trace_id] : undefined,
    turnId: message.turn_id ?? undefined,
  }
}

function toRestorableChatMessage(
  message: ChatHistoryMessage,
  options: RestorableChatMessageOptions = {},
): RestorableChatMessage | null {
  const role = toRestorableRole(message, options)
  if (!role) {
    return null
  }

  const baseMessage = buildRestorableChatMessageBase(message, role)
  const payload = isRecord(message.payload_json) ? message.payload_json : null
  const fileData = extractFileData(payload)

  if (message.message_type === 'file_download' && fileData) {
    return {
      ...baseMessage,
      role: 'assistant',
      type: 'file_download',
      fileData,
    }
  }

  const flowStepEvidence = extractFlowStepEvidence(payload)
  if ((message.message_type === 'flow_step_evidence' || role === 'flow') && flowStepEvidence) {
    return {
      ...baseMessage,
      role: 'flow',
      flowStepEvidence,
      evidenceRecords: flowStepEvidence.evidence_records,
    }
  }

  const evidenceRecords = extractEvidenceRecords(payload?.evidence_records)
  const curationSupport = extractEvidenceCurationSupport(payload)

  return {
    ...baseMessage,
    type: 'text',
    evidenceRecords: evidenceRecords.length > 0 ? evidenceRecords : undefined,
    evidenceCurationSupported: curationSupport?.supported,
    evidenceCurationAdapterKey: curationSupport?.adapterKey ?? undefined,
  }
}

export function buildRestorableChatMessages(
  messages: ChatHistoryMessage[],
  options: RestorableChatMessageOptions = {},
): RestorableChatMessage[] {
  return messages.flatMap((message) => {
    const restorableMessage = toRestorableChatMessage(message, options)
    return restorableMessage ? [restorableMessage] : []
  })
}

function encodeSessionId(sessionId: string): string {
  return encodeURIComponent(sessionId.trim())
}

async function fetchChatHistoryJson<T>(
  path: string,
  init?: RequestInit,
  options: ChatHistoryFetchOptions = {},
): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    credentials: 'include',
    ...init,
    headers,
  })

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  if (options.expectJson === false || response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

export function buildChatHistoryListQueryParams(
  request: ChatHistoryListRequest,
): URLSearchParams {
  const params = new URLSearchParams()
  params.set('chat_kind', request.chatKind)

  if (typeof request.limit === 'number') {
    params.set('limit', String(request.limit))
  }

  const cursor = normalizeChatHistoryValue(request.cursor)
  if (cursor) {
    params.set('cursor', cursor)
  }

  const query = normalizeChatHistoryValue(request.query)
  if (query) {
    params.set('query', query)
  }

  const documentId = normalizeChatHistoryValue(request.documentId)
  if (documentId) {
    params.set('document_id', documentId)
  }

  return params
}

export function buildChatHistoryDetailQueryParams(
  request: ChatHistoryDetailRequest,
): URLSearchParams {
  const params = new URLSearchParams()

  if (typeof request.messageLimit === 'number') {
    params.set('message_limit', String(request.messageLimit))
  }

  const messageCursor = normalizeChatHistoryValue(request.messageCursor)
  if (messageCursor) {
    params.set('message_cursor', messageCursor)
  }

  return params
}

export async function fetchChatHistoryList(
  request: ChatHistoryListRequest,
): Promise<ChatHistoryListResponse> {
  const params = buildChatHistoryListQueryParams(request)
  const query = params.toString()

  return fetchChatHistoryJson<ChatHistoryListResponse>(
    `/api/chat/history${query ? `?${query}` : ''}`,
  )
}

export async function fetchChatHistoryDetail(
  request: ChatHistoryDetailRequest,
): Promise<ChatHistoryDetailResponse> {
  const params = buildChatHistoryDetailQueryParams(request)
  const query = params.toString()

  return fetchChatHistoryJson<ChatHistoryDetailResponse>(
    `/api/chat/history/${encodeSessionId(request.sessionId)}${query ? `?${query}` : ''}`,
  )
}

export async function renameChatSession(
  request: RenameChatSessionRequest,
): Promise<RenameChatSessionResponse> {
  return fetchChatHistoryJson<RenameChatSessionResponse>(
    `/api/chat/session/${encodeSessionId(request.sessionId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ title: request.title }),
    },
  )
}

export async function deleteChatSession(
  request: DeleteChatSessionRequest,
): Promise<void> {
  return fetchChatHistoryJson<void>(
    `/api/chat/session/${encodeSessionId(request.sessionId)}`,
    {
      method: 'DELETE',
    },
    { expectJson: false },
  )
}

export async function bulkDeleteChatSessions(
  request: BulkDeleteChatSessionsRequest,
): Promise<BulkDeleteChatSessionsResponse> {
  return fetchChatHistoryJson<BulkDeleteChatSessionsResponse>(
    '/api/chat/session/bulk-delete',
    {
      method: 'POST',
      body: JSON.stringify({
        session_ids: request.sessionIds,
      }),
    },
  )
}
