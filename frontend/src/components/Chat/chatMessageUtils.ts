import { debug } from '@/utils/env'
import type { CurationWorkspaceLaunchTarget } from '@/features/curation/navigation/openCurationWorkspace'
import type { EvidenceRecord } from '@/features/curation/types'
import type { SSEEvent } from '@/hooks/useChatStream'
import { normalizeOptionalText } from '@/lib/normalizeOptionalText'
import type { ChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

import type {
  Message,
  SerializedMessage,
  StoredChatData,
  TerminalTurnState,
  RescueState,
} from './types'

export function withUpdatedReviewAndCurateSessionId(
  messages: Message[],
  messageId: string,
  sessionId: string,
): Message[] {
  return messages.map((message) => {
    if (message.id !== messageId || !message.reviewAndCurateTarget) {
      return message
    }

    return {
      ...message,
      reviewAndCurateTarget: {
        ...message.reviewAndCurateTarget,
        sessionId,
      },
    }
  })
}

export function buildEvidenceReviewAndCurateTarget(
  documentId?: string | null,
  originSessionId?: string | null,
  adapterKeys?: string[] | null,
): CurationWorkspaceLaunchTarget | null {
  if (!documentId || !originSessionId) {
    return null
  }

  return {
    documentId,
    originSessionId,
    adapterKeys: adapterKeys?.map((value) => value.trim()).filter(Boolean),
  }
}

export function humanizeAdapterKey(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function isEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!value || typeof value !== 'object') {
    return false
  }

  const record = value as Record<string, unknown>

  return (
    typeof record.entity === 'string'
    && typeof record.verified_quote === 'string'
    && typeof record.page === 'number'
    && Number.isFinite(record.page)
    && typeof record.section === 'string'
    && typeof record.chunk_id === 'string'
  )
}

export function extractEvidenceRecords(value: unknown): EvidenceRecord[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.filter(isEvidenceRecord)
}

export function extractFlowStepEvidenceDetails(event: SSEEvent): FlowStepEvidenceDetails | null {
  const candidates: Array<Record<string, unknown>> = []

  if (event.details && typeof event.details === 'object') {
    candidates.push(event.details as Record<string, unknown>)
  }

  candidates.push(event as Record<string, unknown>)

  for (const candidate of candidates) {
    const flowId = typeof candidate.flow_id === 'string' ? candidate.flow_id : null
    const flowName = typeof candidate.flow_name === 'string' ? candidate.flow_name : null
    const flowRunId = typeof candidate.flow_run_id === 'string' ? candidate.flow_run_id : null
    const step = typeof candidate.step === 'number' && Number.isFinite(candidate.step)
      ? candidate.step
      : null
    const evidenceCount =
      typeof candidate.evidence_count === 'number' && Number.isFinite(candidate.evidence_count)
        ? candidate.evidence_count
        : null
    const totalEvidenceRecords =
      typeof candidate.total_evidence_records === 'number'
      && Number.isFinite(candidate.total_evidence_records)
        ? candidate.total_evidence_records
        : null

    if (
      !flowId
      || !flowName
      || !flowRunId
      || step === null
      || evidenceCount === null
      || totalEvidenceRecords === null
    ) {
      continue
    }

    const evidenceRecords = extractEvidenceRecords(candidate.evidence_records)

    return {
      flow_id: flowId,
      flow_name: flowName,
      flow_run_id: flowRunId,
      step,
      tool_name: typeof candidate.tool_name === 'string' ? candidate.tool_name : null,
      agent_id: typeof candidate.agent_id === 'string' ? candidate.agent_id : null,
      agent_name: typeof candidate.agent_name === 'string' ? candidate.agent_name : null,
      evidence_records: evidenceRecords,
      evidence_count: evidenceCount,
      total_evidence_records: totalEvidenceRecords,
    }
  }

  return null
}

export function extractEventTimestamp(event: SSEEvent): Date | null {
  if (typeof event.timestamp !== 'string') {
    return null
  }

  const timestamp = new Date(event.timestamp)
  return Number.isNaN(timestamp.getTime()) ? null : timestamp
}

export function buildTurnId(): string {
  return crypto.randomUUID()
}

export function buildUserTurnMessageId(turnId: string): string {
  return `user-turn-${turnId}`
}

export function buildAssistantTurnMessageId(turnId: string): string {
  return `assistant-turn-${turnId}`
}

export function getEventTurnId(event: SSEEvent): string | null {
  return normalizeOptionalText(event.turn_id)
}

export function mergeTraceIds(existingTraceIds?: string[], traceId?: string | null): string[] | undefined {
  const normalizedTraceId = normalizeOptionalText(traceId)
  if (!normalizedTraceId) {
    return existingTraceIds && existingTraceIds.length > 0 ? existingTraceIds : undefined
  }

  if (existingTraceIds?.includes(normalizedTraceId)) {
    return existingTraceIds
  }

  const nextTraceIds = existingTraceIds ? [...existingTraceIds, normalizedTraceId] : [normalizedTraceId]
  return nextTraceIds
}

export function findAssistantMessageIndex(messages: Message[], turnId: string): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role === 'assistant' && message.turnId === turnId) {
      return index
    }
  }

  return -1
}

export function withTraceIdOnAssistantTurn(
  messages: Message[],
  traceId?: string | null,
  turnId?: string | null,
): Message[] {
  const normalizedTraceId = normalizeOptionalText(traceId)
  if (!normalizedTraceId) {
    return messages
  }

  const nextMessages = [...messages]
  let targetIndex = -1
  if (turnId) {
    targetIndex = findAssistantMessageIndex(nextMessages, turnId)
  } else {
    for (let index = nextMessages.length - 1; index >= 0; index -= 1) {
      if (nextMessages[index].role === 'assistant') {
        targetIndex = index
        break
      }
    }
  }

  if (targetIndex === -1) {
    return messages
  }

  const targetMessage = nextMessages[targetIndex]
  const nextTraceIds = mergeTraceIds(targetMessage.traceIds, normalizedTraceId)
  if (nextTraceIds === targetMessage.traceIds) {
    return messages
  }

  nextMessages[targetIndex] = {
    ...targetMessage,
    traceIds: nextTraceIds,
  }
  return nextMessages
}

export function upsertAssistantTurnMessage(
  messages: Message[],
  options: {
    turnId: string
    content?: string
    timestamp?: Date
    traceId?: string | null
    terminalState?: TerminalTurnState | null
    terminalMessage?: string | null
    rescueState?: RescueState
  },
): Message[] {
  const existingIndex = findAssistantMessageIndex(messages, options.turnId)
  const existingMessage = existingIndex >= 0 ? messages[existingIndex] : null
  const terminalMessage = options.terminalMessage ?? existingMessage?.terminalMessage ?? null
  const nextContent = options.content ?? existingMessage?.content ?? terminalMessage ?? ''

  const nextMessage: Message = {
    ...(existingMessage ?? {}),
    role: 'assistant',
    id: existingMessage?.id ?? buildAssistantTurnMessageId(options.turnId),
    turnId: options.turnId,
    content: nextContent,
    timestamp: existingMessage?.timestamp ?? options.timestamp ?? new Date(),
    traceIds: mergeTraceIds(existingMessage?.traceIds, options.traceId),
    terminalState: options.terminalState ?? existingMessage?.terminalState ?? null,
    terminalMessage,
    rescueState: options.rescueState ?? existingMessage?.rescueState ?? null,
  }

  if (existingIndex === -1) {
    return [...messages, nextMessage]
  }

  const nextMessages = [...messages]
  nextMessages[existingIndex] = nextMessage
  return nextMessages
}

export function getAssistantStatusNotice(message: Message): {
  tone: 'info' | 'warning' | 'error'
  text: string
} | null {
  switch (message.terminalState) {
    case 'turn_interrupted':
      return {
        tone: 'warning',
        text: message.terminalMessage ?? 'The response was interrupted before it could be saved.',
      }
    case 'turn_failed':
      return {
        tone: 'error',
        text: message.terminalMessage ?? 'The response failed before it could be saved.',
      }
    case 'turn_save_failed':
      if (message.rescueState === 'pending') {
        return {
          tone: 'info',
          text: 'Saving this response to chat history...',
        }
      }

      if (message.rescueState === 'failed') {
        return {
          tone: 'error',
          text: message.terminalMessage ?? 'This response could not be saved to chat history.',
        }
      }

      return null
    case 'session_gone':
      return {
        tone: 'error',
        text: message.terminalMessage ?? 'This chat session is no longer available.',
      }
    default:
      return null
  }
}

export function getTerminalTurnDefaultMessage(
  state: Exclude<TerminalTurnState, 'turn_completed'>,
): string {
  switch (state) {
    case 'turn_interrupted':
      return 'The response was interrupted before it could be saved.'
    case 'turn_failed':
      return 'The response failed before it could be saved.'
    case 'turn_save_failed':
      return 'The response completed, but it could not be saved to chat history.'
    case 'session_gone':
      return 'This chat session is no longer available.'
    default:
      return 'The response could not be completed.'
  }
}

export function withFlowStepEvidenceMessage(
  messages: Message[],
  flowStepEvidence: FlowStepEvidenceDetails,
  timestamp: Date,
): Message[] {
  const existingIndex = messages.findIndex((message) =>
    message.role === 'flow'
    && message.flowStepEvidence?.flow_run_id === flowStepEvidence.flow_run_id
    && message.flowStepEvidence?.step === flowStepEvidence.step
    && message.flowStepEvidence?.tool_name === flowStepEvidence.tool_name
  )

  const nextMessage: Message = {
    role: 'flow',
    content: '',
    timestamp,
    id: `flow-evidence-${flowStepEvidence.flow_run_id}-${flowStepEvidence.step}-${flowStepEvidence.tool_name ?? flowStepEvidence.agent_id ?? 'step'}`,
    flowStepEvidence,
    evidenceRecords: flowStepEvidence.evidence_records,
  }

  if (existingIndex === -1) {
    return [...messages, nextMessage]
  }

  const nextMessages = [...messages]
  nextMessages[existingIndex] = {
    ...nextMessages[existingIndex],
    ...nextMessage,
  }
  return nextMessages
}

const DUPLICATE_EVIDENCE_LABEL_RE = /^(?:[-*]\s+)?\*{0,2}(evidence|citations|sources)\*{0,2}:/i

export function stripDuplicateEvidenceSections(content: string): string {
  if (!content) {
    return content
  }

  const lines = content.split('\n')
  const keptLines: string[] = []
  let skippingBlock = false
  let skipIndent = 0

  for (const line of lines) {
    const trimmed = line.trim()
    const indent = line.length - line.trimStart().length

    if (DUPLICATE_EVIDENCE_LABEL_RE.test(trimmed)) {
      skippingBlock = true
      skipIndent = indent
      continue
    }

    if (skippingBlock) {
      if (!trimmed) {
        continue
      }

      const isContinuation = indent > skipIndent || /^[>*-]/.test(trimmed)
      if (isContinuation) {
        continue
      }

      skippingBlock = false
    }

    keptLines.push(line)
  }

  return keptLines
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export function sanitizeStoredMessage(message: SerializedMessage): Message {
  const hasEvidenceRecords = (message.evidenceRecords?.length ?? 0) > 0
  const hasExplicitEvidenceCurationMetadata =
    typeof message.evidenceCurationSupported === 'boolean'
    || Boolean(message.evidenceCurationAdapterKey)
  const hasAdapterScopedReviewTarget =
    Array.isArray(message.reviewAndCurateTarget?.adapterKeys)
    && message.reviewAndCurateTarget.adapterKeys.length > 0
  const messageContent = normalizeOptionalText(message.content)
  const terminalMessage = normalizeOptionalText(message.terminalMessage)
  if (!messageContent && !terminalMessage) {
    console.error('[Chat] Restored message was missing display content:', message)
    return {
      ...message,
      content: '[Message content unavailable]',
      terminalMessage: null,
      timestamp: new Date(message.timestamp),
    }
  }

  return {
    ...message,
    content: hasEvidenceRecords
      ? stripDuplicateEvidenceSections(messageContent || terminalMessage || '')
      : (messageContent || terminalMessage || ''),
    reviewAndCurateTarget:
      hasEvidenceRecords
      && !hasExplicitEvidenceCurationMetadata
      && !hasAdapterScopedReviewTarget
        ? null
        : message.reviewAndCurateTarget,
    timestamp: new Date(message.timestamp),
  }
}

function withEvidenceReviewAndCurateTarget(
  message: Message,
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null,
): Message {
  if (!reviewAndCurateTarget) {
    return message
  }

  if (message.reviewAndCurateTarget?.sessionId) {
    return message
  }

  return {
    ...message,
    reviewAndCurateTarget,
  }
}

export function withEvidenceRecords(
  messages: Message[],
  evidenceRecords: EvidenceRecord[],
  options?: {
    turnId?: string | null
    reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
    evidenceCurationSupported?: boolean | null
    evidenceCurationAdapterKey?: string | null
  },
): Message[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (
      message.role !== 'assistant'
      || (options?.turnId && message.turnId !== options.turnId)
    ) {
      continue
    }

    const nextMessages = [...messages]
    nextMessages[index] = withEvidenceReviewAndCurateTarget({
      ...message,
      content: stripDuplicateEvidenceSections(message.content),
      evidenceRecords,
      evidenceCurationSupported: options?.evidenceCurationSupported ?? message.evidenceCurationSupported ?? null,
      evidenceCurationAdapterKey: options?.evidenceCurationAdapterKey ?? message.evidenceCurationAdapterKey ?? null,
    }, options?.reviewAndCurateTarget)
    return nextMessages
  }

  return messages
}

export function withMissingEvidenceReviewAndCurateTargets(
  messages: Message[],
  documentId?: string | null,
  originSessionId?: string | null,
): Message[] {
  if (!documentId || !originSessionId) {
    return messages
  }

  let didChange = false
  const nextMessages = messages.map((message) => {
    if (
      message.role !== 'assistant'
      || (message.evidenceRecords?.length ?? 0) === 0
      || message.reviewAndCurateTarget
      || message.evidenceCurationSupported !== true
      || !message.evidenceCurationAdapterKey
    ) {
      return message
    }

    const reviewAndCurateTarget = buildEvidenceReviewAndCurateTarget(
      documentId,
      originSessionId,
      [message.evidenceCurationAdapterKey],
    )
    if (!reviewAndCurateTarget) {
      return message
    }

    didChange = true
    return {
      ...message,
      reviewAndCurateTarget,
    }
  })

  return didChange ? nextMessages : messages
}

export function shouldShowCurationDbWarning(status?: string | null): boolean {
  return status !== 'connected' && status !== 'not_configured'
}

export function loadMessagesFromStorage(
  storageKeys: ChatLocalStorageKeys | null,
  sessionId?: string | null,
): Message[] {
  try {
    if (!storageKeys) {
      return []
    }

    const stored = localStorage.getItem(storageKeys.messages)
    const currentSessionId = sessionId ?? localStorage.getItem(storageKeys.sessionId)
    debug.log('[Chat] loadMessagesFromStorage called:', {
      hasStoredMessages: !!stored,
      storedLength: stored?.length || 0,
      currentSessionId: currentSessionId || 'none',
    })

    if (stored) {
      const data = JSON.parse(stored) as StoredChatData | SerializedMessage[]

      if ('session_id' in data && 'messages' in data) {
        debug.log('[Chat] Found new format with session_id:', {
          storedSessionId: data.session_id,
          currentSessionId,
          match: data.session_id === currentSessionId,
          messageCount: data.messages.length,
        })
        if (data.session_id === currentSessionId) {
          debug.log('[Chat] Session match - restoring messages')
          return data.messages.map(sanitizeStoredMessage)
        }

        debug.log('[Chat] Session mismatch - skipping restore for current session')
        return []
      }

      if (Array.isArray(data)) {
        debug.log('[Chat] Found legacy format (no session_id), restoring', data.length, 'messages')
        return data.map(sanitizeStoredMessage)
      }
    }
  } catch (error) {
    console.warn('Failed to load messages from localStorage:', error)
  }
  debug.log('[Chat] No messages to restore')
  return []
}
