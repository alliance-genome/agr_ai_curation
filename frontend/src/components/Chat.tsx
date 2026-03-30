import React, { useState, useRef, useEffect, useCallback } from 'react'

import { debug } from '@/utils/env'
import { dispatchClearHighlights, dispatchPDFDocumentChanged } from '@/components/pdfViewer/pdfEvents'
import MessageActions from '@/components/Chat/MessageActions'
import FeedbackDialog from '@/components/Chat/FeedbackDialog'
import FileDownloadCard, { FileInfo } from '@/components/Chat/FileDownloadCard'
import EvidenceCard from '@/components/Chat/EvidenceCard'
import PrepScopeConfirmationDialog from '@/features/curation/components/PrepScopeConfirmationDialog'
import {
  fetchCurationPrepPreview,
  runCurationPrep,
  type CurationPrepPreview,
} from '@/features/curation/services/curationPrepService'
import {
  openCurationWorkspace,
  type CurationWorkspaceLaunchTarget,
} from '@/features/curation/navigation/openCurationWorkspace'
import type { EvidenceRecord } from '@/features/curation/types'
import { submitFeedback } from '@/services/feedbackService'
import { useAuth } from '@/contexts/AuthContext'
import type { SSEEvent } from '@/hooks/useChatStream'
import { emitGlobalToast } from '@/lib/globalNotifications'
import { useNavigate } from 'react-router-dom'

// localStorage key for chat messages (shared with HomePage)
const CHAT_MESSAGES_KEY = 'chat-messages'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  id?: string
  traceIds?: string[]
  type?: 'text' | 'file_download'  // Message type for special rendering
  fileData?: FileInfo              // File info for file_download type
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
  evidenceRecords?: EvidenceRecord[]
}

// Type for serialized messages (timestamp as string)
interface SerializedMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  id?: string
  traceIds?: string[]
  type?: 'text' | 'file_download'
  fileData?: FileInfo
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
  evidenceRecords?: EvidenceRecord[]
}

interface ActiveDocument {
  id: string
  filename?: string | null
  chunk_count?: number | null
  vector_count?: number | null
}

interface ConversationStatus {
  is_active: boolean
  conversation_id?: string | null
  memory_stats?: {
    memory_sizes?: {
      short_term?: { file_count: number; size_mb: number }
      long_term?: { file_count: number; size_mb: number }
      entity?: { file_count: number; size_mb: number }
    }
  }
}

interface ChatProps {
  /**
   * Session ID passed from parent (HomePage)
   * Used to scope messages and sync with audit panel
   */
  sessionId: string | null

  /**
   * Callback to notify parent when session ID changes (e.g., after reset)
   * Parent (HomePage) must update its session state when this is called
   */
  onSessionChange?: (newSessionId: string) => void

  /**
   * Shared SSE events from useChatStream hook (lifted to HomePage)
   */
  events: SSEEvent[]

  /**
   * Loading state from useChatStream hook
   */
  isLoading: boolean

  /**
   * Send message function from useChatStream hook
   */
  sendMessage: (message: string, sessionId: string) => Promise<void>
}

// Storage data structure with session validation
interface StoredChatData {
  session_id: string | null
  messages: SerializedMessage[]
}

function withUpdatedReviewAndCurateSessionId(
  messages: Message[],
  messageId: string,
  sessionId: string
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

function buildEvidenceReviewAndCurateTarget(
  documentId?: string | null,
  originSessionId?: string | null
): CurationWorkspaceLaunchTarget | null {
  if (!documentId || !originSessionId) {
    return null
  }

  return {
    documentId,
    originSessionId,
  }
}

function isEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!value || typeof value !== 'object') {
    return false
  }

  const record = value as Record<string, unknown>

  return (
    typeof record.entity === 'string' &&
    typeof record.verified_quote === 'string' &&
    typeof record.page === 'number' &&
    Number.isFinite(record.page) &&
    typeof record.section === 'string' &&
    typeof record.chunk_id === 'string'
  )
}

function extractEvidenceRecords(value: unknown): EvidenceRecord[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.filter(isEvidenceRecord)
}

const DUPLICATE_EVIDENCE_LABEL_RE = /^(?:[-*]\s+)?\*{0,2}(evidence|citations|sources)\*{0,2}:/i

function stripDuplicateEvidenceSections(content: string): string {
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

function sanitizeStoredMessage(message: SerializedMessage): Message {
  const hasEvidenceRecords = (message.evidenceRecords?.length ?? 0) > 0

  return {
    ...message,
    content: hasEvidenceRecords
      ? stripDuplicateEvidenceSections(message.content)
      : message.content,
    timestamp: new Date(message.timestamp),
  }
}
function withEvidenceReviewAndCurateTarget(
  message: Message,
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
): Message {
  if (message.reviewAndCurateTarget || !reviewAndCurateTarget) {
    return message
  }

  return {
    ...message,
    reviewAndCurateTarget,
  }
}

function withEvidenceRecords(
  messages: Message[],
  evidenceRecords: EvidenceRecord[],
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
): Message[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'assistant') {
      continue
    }

    const nextMessages = [...messages]
    nextMessages[index] = withEvidenceReviewAndCurateTarget({
      ...message,
      content: stripDuplicateEvidenceSections(message.content),
      evidenceRecords,
    }, reviewAndCurateTarget)
    return nextMessages
  }

  return messages
}

function withMissingEvidenceReviewAndCurateTargets(
  messages: Message[],
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
): Message[] {
  if (!reviewAndCurateTarget) {
    return messages
  }

  let didChange = false
  const nextMessages = messages.map((message) => {
    if (
      message.role !== 'assistant' ||
      (message.evidenceRecords?.length ?? 0) === 0 ||
      message.reviewAndCurateTarget
    ) {
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

function shouldShowCurationDbWarning(status?: string | null): boolean {
  return status !== 'connected' && status !== 'not_configured'
}

// Helper to load messages from localStorage with session validation
function loadMessagesFromStorage(sessionId?: string | null): Message[] {
  try {
    const stored = localStorage.getItem(CHAT_MESSAGES_KEY)
    const currentSessionId = sessionId ?? localStorage.getItem(CHAT_SESSION_ID_KEY)
    debug.log('[Chat] loadMessagesFromStorage called:', {
      hasStoredMessages: !!stored,
      storedLength: stored?.length || 0,
      currentSessionId: currentSessionId || 'none'
    })

    if (stored) {
      const data = JSON.parse(stored) as StoredChatData | SerializedMessage[]

      // Handle new format with session_id
      if ('session_id' in data && 'messages' in data) {
        debug.log('[Chat] Found new format with session_id:', {
          storedSessionId: data.session_id,
          currentSessionId,
          match: data.session_id === currentSessionId,
          messageCount: data.messages.length
        })
        // Only restore messages if they belong to the current session
        if (data.session_id === currentSessionId) {
          debug.log('[Chat] Session match - restoring messages')
          return data.messages.map(sanitizeStoredMessage)
        } else {
          // Session mismatch can happen transiently during route/session initialization.
          // Do not delete stored chat here; keep it available if session state catches up.
          debug.log('[Chat] Session mismatch - skipping restore for current session')
          return []
        }
      }

      // Handle legacy format (array of messages without session_id)
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

// localStorage key for session ID (shared with HomePage)
const CHAT_SESSION_ID_KEY = 'chat-session-id'

function Chat({
  sessionId: propSessionId,
  onSessionChange,
  events,
  isLoading,
  sendMessage
}: ChatProps) {
  const navigate = useNavigate()
  // Initialize messages from localStorage if available
  const [messages, setMessages] = useState<Message[]>(() => loadMessagesFromStorage(propSessionId))
  const [inputMessage, setInputMessage] = useState('')
  const [progressMessage, setProgressMessage] = useState<string>('')
  const [activeDocument, setActiveDocument] = useState<ActiveDocument | null>(null)
  const [weaviateConnected, setWeaviateConnected] = useState(true)
  const [showCurationDbWarning, setShowCurationDbWarning] = useState(false)
  const [conversationStatus, setConversationStatus] = useState<ConversationStatus | null>(null)
  const [isResetting, setIsResetting] = useState(false)
  const [isUnloadingPDF, setIsUnloadingPDF] = useState(false)
  const [feedbackDialogOpen, setFeedbackDialogOpen] = useState(false)
  const [feedbackMessageData, setFeedbackMessageData] = useState<{
    content: string
    traceIds: string[]
  } | null>(null)
  const [prepDialogOpen, setPrepDialogOpen] = useState(false)
  const [prepPreview, setPrepPreview] = useState<CurationPrepPreview | null>(null)
  const [isLoadingPrepPreview, setIsLoadingPrepPreview] = useState(false)
  const [isPreparingCuration, setIsPreparingCuration] = useState(false)
  const [prepDialogError, setPrepDialogError] = useState<string | null>(null)
  const [prepStatus, setPrepStatus] = useState<{
    kind: 'success' | 'error' | 'info'
    message: string
  } | null>(null)
  const [refinePrompt, setRefinePrompt] = useState<string | null>(null)
  const [refineText, setRefineText] = useState<string>('')
  const [limitNotices, setLimitNotices] = useState<string[]>([])
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Get authenticated user for feedback submissions
  const { user } = useAuth()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const assistantMessageIdRef = useRef<string | null>(null)
  const progressMessageQueueRef = useRef<string[]>([])
  const progressMessageTimerRef = useRef<NodeJS.Timeout | null>(null)
  const lastProgressUpdateRef = useRef<number>(0)
  const assistantMessageRef = useRef<string>('')
  const processedEventIdsRef = useRef<Set<number>>(new Set())
  const latestMessagesRef = useRef<Message[]>(messages)
  const latestSessionIdRef = useRef<string | null>(propSessionId)
  const persistTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const restoredSessionRef = useRef<string | null>(null)

  // Track ALL trace IDs from this session for feedback
  const sessionTraceIds = useRef<string[]>([])

  // Keep "latest" refs synchronized during render to avoid stale values during unmount cleanup.
  latestMessagesRef.current = messages
  latestSessionIdRef.current = propSessionId

  const persistMessagesToStorage = useCallback((nextMessages: Message[], sessionId: string | null) => {
    try {
      if (!sessionId) return

      if (nextMessages.length === 0) {
        localStorage.removeItem(CHAT_MESSAGES_KEY)
        return
      }

      const serialized: SerializedMessage[] = nextMessages.map(msg => ({
        ...msg,
        timestamp: msg.timestamp.toISOString()
      }))
      const storageData: StoredChatData = {
        session_id: sessionId,
        messages: serialized
      }
      localStorage.setItem(CHAT_MESSAGES_KEY, JSON.stringify(storageData))
    } catch (error) {
      console.warn('Failed to persist messages to localStorage:', error)
    }
  }, [])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  /**
   * Filter function to determine which audit events should show in chat progress
   * T026: shouldShowInChat filtering
   *
   * Chat shows: SUPERVISOR_START, CREW_START, TOOL_START, TOOL_COMPLETE, LLM_CALL, SUPERVISOR_COMPLETE
   * Audit only: SUPERVISOR_DISPATCH, AGENT_COMPLETE, SUPERVISOR_RESULT
   */
  const shouldShowInChat = (eventType: string): boolean => {
    const chatEvents = [
      'SUPERVISOR_START',
      'CREW_START',
      'TOOL_START',
      'TOOL_COMPLETE',
      'LLM_CALL',
      'SUPERVISOR_COMPLETE',
      'DOMAIN_SKIPPED',
      'DOMAIN_WARNING',
      'PENDING_USER_INPUT',
      'STOP_CONFIRMED'
    ]
    return chatEvents.includes(eventType)
  }

  /**
   * Convert audit event to friendly progress message
   * Extracts friendlyName from event details for user-friendly display
   */
  const getFriendlyProgressMessage = (event: SSEEvent): string => {
    switch (event.type) {
      case 'SUPERVISOR_START':
        return event.details?.message || 'Starting...'
      case 'STOP_CONFIRMED':
        return 'Interaction stopped by user'

      case 'CREW_START':
        // Use crewDisplayName from backend dispatch dictionary
        return event.details?.crewDisplayName || `Starting ${event.details?.crewName || 'crew'}...`

      case 'TOOL_START':
        // Use friendlyName from backend dispatch dictionary
        return event.details?.friendlyName || `Using ${event.details?.toolName || 'tool'}...`

      case 'TOOL_COMPLETE':
        // Use friendlyName from backend; don't add "complete" if already present
        if (event.details?.friendlyName) {
          const name = event.details.friendlyName
          return name.toLowerCase().endsWith('complete') ? name : `${name} complete`
        }
        return 'Tool complete'

      case 'LLM_CALL':
        return event.details?.message || 'Thinking...'

      case 'DOMAIN_WARNING':
        return event.details?.message || 'Warning received.'

      case 'PENDING_USER_INPUT':
        return event.details?.message || 'Action required: please refine the query (limit/filter).'

      case 'DOMAIN_SKIPPED':
        return event.details?.message || 'Action required: please refine the query (limit/filter).'

      case 'SUPERVISOR_COMPLETE':
        return event.details?.message || 'Complete'

      default:
        return 'Processing...'
    }
  }

  // Minimum time to display each progress message (in ms)
  const MIN_PROGRESS_DISPLAY_TIME = 1800

  const updateProgressMessage = useCallback((newMessage: string) => {
    const now = Date.now()
    const timeSinceLastUpdate = now - lastProgressUpdateRef.current

    if (timeSinceLastUpdate >= MIN_PROGRESS_DISPLAY_TIME) {
      // Enough time has passed, update immediately
      setProgressMessage(newMessage)
      lastProgressUpdateRef.current = now

      // Process next queued message if any
      if (progressMessageQueueRef.current.length > 0) {
        const nextMessage = progressMessageQueueRef.current.shift()!
        progressMessageTimerRef.current = setTimeout(() => {
          updateProgressMessage(nextMessage)
        }, MIN_PROGRESS_DISPLAY_TIME)
      }
    } else {
      // Not enough time has passed, queue the message
      progressMessageQueueRef.current.push(newMessage)

      // Set timer if not already set
      if (!progressMessageTimerRef.current) {
        const delay = MIN_PROGRESS_DISPLAY_TIME - timeSinceLastUpdate
        progressMessageTimerRef.current = setTimeout(() => {
          progressMessageTimerRef.current = null
          if (progressMessageQueueRef.current.length > 0) {
            const nextMessage = progressMessageQueueRef.current.shift()!
            updateProgressMessage(nextMessage)
          }
        }, delay)
      }
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // If session arrives after mount (or changes), restore persisted messages once per session.
  useEffect(() => {
    if (!propSessionId || messages.length > 0 || restoredSessionRef.current === propSessionId) return

    restoredSessionRef.current = propSessionId
    const restored = loadMessagesFromStorage(propSessionId)
    if (restored.length > 0) {
      setMessages(restored)
    }
  }, [propSessionId, messages.length])

  // Persist messages to localStorage whenever they change (debounced to avoid rapid writes during streaming)
  useEffect(() => {
    if (!propSessionId || messages.length === 0) return

    if (persistTimeoutRef.current) {
      clearTimeout(persistTimeoutRef.current)
      persistTimeoutRef.current = null
    }

    persistTimeoutRef.current = setTimeout(() => {
      persistMessagesToStorage(messages, propSessionId)
      persistTimeoutRef.current = null
    }, 500) // 500ms debounce to avoid excessive writes during streaming

    return () => {
      if (persistTimeoutRef.current) {
        clearTimeout(persistTimeoutRef.current)
        persistTimeoutRef.current = null
      }
    }
  }, [messages, propSessionId, persistMessagesToStorage])

  // Flush latest chat state on unmount so navigation does not lose pending debounced updates.
  useEffect(() => {
    return () => {
      if (persistTimeoutRef.current) {
        clearTimeout(persistTimeoutRef.current)
        persistTimeoutRef.current = null
      }
      persistMessagesToStorage(latestMessagesRef.current, latestSessionIdRef.current)
    }
  }, [persistMessagesToStorage])

  useEffect(() => {
    if (isLoading) {
      scrollToBottom()
    }
  }, [isLoading])

  // Process SSE events from useChatStream hook
  useEffect(() => {
    // Process only new events (track by array index)
    const newEvents = events.slice(processedEventIdsRef.current.size)

    newEvents.forEach((parsed: SSEEvent) => {
      debug.log('🔍 [SSE] Processing event:', parsed.type, parsed)

      // RUN_STARTED
      if (parsed.type === 'RUN_STARTED') {
        window.dispatchEvent(new CustomEvent('pdf-overlay-clear'))
        updateProgressMessage('Starting...')
        // Capture trace_id early so it's available for STOP_CONFIRMED
        if (parsed.trace_id && !sessionTraceIds.current.includes(parsed.trace_id)) {
          debug.log('🔍 [TRACE] Captured trace ID from RUN_STARTED:', parsed.trace_id)
          sessionTraceIds.current.push(parsed.trace_id)
        }
        return
      }

      // STOP_CONFIRMED (synthetic event when user clicks Stop)
      if (parsed.type === 'STOP_CONFIRMED') {
        updateProgressMessage('Interaction stopped by user')
        // Append an assistant message so feedback button appears
        // Use accumulated session trace IDs since STOP_CONFIRMED is synthetic
        const stopMessage = 'Query stopped. Problems? Click Feedback (⋮) to report.'
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant',
            content: stopMessage,
            timestamp: new Date(),
            id: `msg-${Date.now()}`,
            traceIds: [...sessionTraceIds.current]
          }
        ])
        return
      }

      // RUN_ERROR (surfaced on stop or actual errors)
      if (parsed.type === 'RUN_ERROR') {
        const stopMessage = 'Query stopped. Problems? Click Feedback (⋮) to report.'
        // Use trace_id from error event, or fall back to session trace IDs
        const errorTraceIds = parsed.trace_id ? [parsed.trace_id] : [...sessionTraceIds.current]
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant',
            content: stopMessage,
            timestamp: new Date(),
            id: `msg-${Date.now()}`,
            traceIds: errorTraceIds
          }
        ])
        return
      }

      // T026: Filter audit events for chat progress display
      // Check if this is an audit event type that should show in chat
      if (shouldShowInChat(parsed.type)) {
        const friendlyMessage = getFriendlyProgressMessage(parsed)
        debug.log('🔍 [AUDIT→CHAT] Showing filtered audit event in chat progress:', parsed.type, friendlyMessage)
        updateProgressMessage(friendlyMessage)

        // Capture refinement prompts for bulk guardrails or pending input
        if (
          parsed.details?.reason === 'bulk_guardrail' &&
          (parsed.type === 'DOMAIN_SKIPPED' || parsed.type === 'PENDING_USER_INPUT' || parsed.type === 'DOMAIN_WARNING')
        ) {
          setRefinePrompt(parsed.details?.message || 'Please provide a limit or species filter to continue.')
        }

        // Capture applied_limit/warnings notices from tool completions
        const appliedLimit = parsed.details?.applied_limit
        const warnings = parsed.details?.warnings
        if (appliedLimit || warnings) {
          const warningText = Array.isArray(warnings) ? warnings.join('; ') : (warnings || '')
          const notice = `Applied limit: ${appliedLimit ?? 'n/a'}${warningText ? ` | Warnings: ${warningText}` : ''}`
          setLimitNotices(prev => prev.includes(notice) ? prev : [...prev, notice])
        }

        return
      }

      // PROGRESS events (legacy string-based progress)
      if (parsed.type === 'PROGRESS') {
        debug.log('🔍 [PROGRESS] Received progress event:', parsed.message)
        updateProgressMessage(parsed.message || 'Processing...')
        return
      }

      // CHUNK_PROVENANCE
      if (parsed.type === 'CHUNK_PROVENANCE') {
        debug.log('🎯 [HIGHLIGHT DEBUG] ============================================')
        debug.log('🎯 [HIGHLIGHT DEBUG] CHUNK_PROVENANCE EVENT RECEIVED IN CHAT.TSX')
        debug.log('🎯 [HIGHLIGHT DEBUG] ============================================')
        debug.log('🎯 [HIGHLIGHT DEBUG] Full parsed event:', JSON.stringify(parsed, null, 2))
        debug.log('🎯 [HIGHLIGHT DEBUG] chunk_id:', parsed.chunk_id)
        debug.log('🎯 [HIGHLIGHT DEBUG] doc_items:', parsed.doc_items)
        debug.log('🎯 [HIGHLIGHT DEBUG] doc_items type:', typeof parsed.doc_items)
        debug.log('🎯 [HIGHLIGHT DEBUG] doc_items is array:', Array.isArray(parsed.doc_items))
        debug.log('🎯 [HIGHLIGHT DEBUG] doc_items length:', parsed.doc_items?.length)
        if (parsed.doc_items && parsed.doc_items.length > 0) {
          debug.log('🎯 [HIGHLIGHT DEBUG] First doc_item:', JSON.stringify(parsed.doc_items[0], null, 2))
          debug.log('🎯 [HIGHLIGHT DEBUG] First doc_item has page:', 'page' in (parsed.doc_items[0] || {}))
          debug.log('🎯 [HIGHLIGHT DEBUG] First doc_item has bbox:', 'bbox' in (parsed.doc_items[0] || {}))
        }
        debug.log('🔍 [CHAT DEBUG] Received CHUNK_PROVENANCE event:', {
          chunk_id: parsed.chunk_id,
          document_id: parsed.document_id,
          active_document_id: activeDocument?.id,
          doc_items_count: parsed.doc_items?.length || 0,
          doc_items: parsed.doc_items
        })

        if (parsed.document_id && activeDocument?.id && parsed.document_id !== activeDocument.id) {
          debug.log('🔍 [CHAT DEBUG] Skipping provenance - document ID mismatch:', {
            received: parsed.document_id,
            active: activeDocument.id
          })
          return
        }

        const docItems = Array.isArray(parsed.doc_items) ? parsed.doc_items : []
        debug.log('🔍 [CHAT DEBUG] Processing doc_items:', {
          count: docItems.length,
          items: docItems.slice(0, 3)
        })

        if (docItems.length > 0 && typeof parsed.chunk_id === 'string' && parsed.chunk_id.trim().length > 0) {
          const overlayDetail = {
            chunkId: parsed.chunk_id,
            documentId: parsed.document_id ?? activeDocument?.id ?? null,
            docItems,
          }
          debug.log('🔍 [CHAT DEBUG] Dispatching pdf-overlay-update event:', overlayDetail)

          window.dispatchEvent(
            new CustomEvent('pdf-overlay-update', {
              detail: overlayDetail
            })
          )
        } else {
          debug.log('🔍 [CHAT DEBUG] Skipping overlay dispatch:', {
            hasDocItems: docItems.length > 0,
            hasChunkId: typeof parsed.chunk_id === 'string' && parsed.chunk_id.trim().length > 0,
            chunk_id: parsed.chunk_id
          })
        }
        return
      }

      // Capture trace_id for feedback
      if (parsed.trace_id) {
        // Add to session-wide trace IDs
        if (!sessionTraceIds.current.includes(parsed.trace_id)) {
          debug.log('🔍 [TRACE] Captured trace ID:', parsed.trace_id)
          sessionTraceIds.current.push(parsed.trace_id)
        }

        // Attach to the current assistant message being streamed
        setMessages(prev => {
          const lastMsg = prev[prev.length - 1]
          if (lastMsg && lastMsg.role === 'assistant') {
            const currentTraceIds = lastMsg.traceIds || []
            if (!currentTraceIds.includes(parsed.trace_id!)) {
               return [
                ...prev.slice(0, -1),
                { 
                  ...lastMsg, 
                  traceIds: [...currentTraceIds, parsed.trace_id!] 
                }
              ]
            }
          }
          return prev
        })
      }

      // TEXT_MESSAGE_CONTENT
      const messageContent = parsed.content || parsed.delta
      if (messageContent && parsed.type === 'TEXT_MESSAGE_CONTENT') {
        assistantMessageRef.current += messageContent

        setMessages(prev => {
          const lastMsg = prev[prev.length - 1]
          if (lastMsg && lastMsg.role === 'assistant') {
            assistantMessageIdRef.current = lastMsg.id ?? assistantMessageIdRef.current
            return [
              ...prev.slice(0, -1),
              { ...lastMsg, content: assistantMessageRef.current }
            ]
          } else {
            const newId = `msg-${Date.now()}`
            assistantMessageIdRef.current = newId
            return [
              ...prev,
              {
                role: 'assistant',
                content: assistantMessageRef.current,
                timestamp: new Date(),
                id: newId,
                traceIds: [] // Initialize traceIds for new message
              }
            ]
          }
        })
      }

      // CHAT_OUTPUT_READY - flow chat output is finalized in a tool call
      // Emit an assistant message so the user sees the actual flow result text.
      if (parsed.type === 'CHAT_OUTPUT_READY' && parsed.details) {
        const outputText = String(parsed.details.output || parsed.details.output_preview || '').trim()
        if (outputText) {
          setMessages(prev => {
            const lastMsg = prev[prev.length - 1]
            // Avoid duplicate append if the same output is already the latest assistant message
            if (lastMsg?.role === 'assistant' && lastMsg.content.trim() === outputText) {
              return prev
            }
            return [
              ...prev,
              {
                role: 'assistant',
                content: outputText,
                timestamp: new Date(),
                id: `msg-${Date.now()}`,
                traceIds: [...sessionTraceIds.current]
              }
            ]
          })
          assistantMessageRef.current = ''
          assistantMessageIdRef.current = null
        }
        return
      }

      // FILE_READY - create a file download message
      if (parsed.type === 'FILE_READY' && parsed.details) {
        const fileData: FileInfo = {
          file_id: parsed.details.file_id,
          filename: parsed.details.filename,
          format: parsed.details.format,
          size_bytes: parsed.details.size_bytes,
          mime_type: parsed.details.mime_type,
          download_url: parsed.details.download_url,
          created_at: parsed.details.created_at,
        }
        debug.log('🔍 [FILE_READY] File ready for download:', fileData.filename)

        setMessages(prev => [
          ...prev,
          {
            role: 'assistant',
            content: `File ready: ${fileData.filename}`,
            timestamp: new Date(),
            id: `file-${Date.now()}`,
            type: 'file_download',
            fileData,
            traceIds: [...sessionTraceIds.current]
          }
        ])
        return
      }

      if (parsed.type === 'evidence_summary') {
        const evidenceRecords = extractEvidenceRecords(parsed.evidence_records)
        if (evidenceRecords.length === 0) {
          return
        }

        const reviewAndCurateTarget = buildEvidenceReviewAndCurateTarget(
          activeDocument?.id,
          propSessionId,
        )

        setMessages(prev => withEvidenceRecords(prev, evidenceRecords, reviewAndCurateTarget))
      }
    })

    // Mark all new events as processed
    processedEventIdsRef.current = new Set(Array.from({ length: events.length }, (_, i) => i))
  }, [events, activeDocument, propSessionId, updateProgressMessage])

  useEffect(() => {
    const reviewAndCurateTarget = buildEvidenceReviewAndCurateTarget(
      activeDocument?.id,
      propSessionId,
    )

    if (!reviewAndCurateTarget) {
      return
    }

    setMessages(prev => withMissingEvidenceReviewAndCurateTargets(prev, reviewAndCurateTarget))
  }, [activeDocument?.id, propSessionId])

  // Update conversation status when messages change (to update memory counter)
  useEffect(() => {
    const updateConversationStatus = async () => {
      try {
        const response = await fetch('/api/chat/conversation')
        if (response.ok) {
          const data = await response.json()
          setConversationStatus(data)
        }
      } catch (error) {
        console.warn('Failed to fetch conversation status', error)
      }
    }

    // Only update if we have messages and the last message is from assistant
    // (indicating a response was just completed)
    if (messages.length > 0 && messages[messages.length - 1].role === 'assistant') {
      updateConversationStatus()
    }
  }, [messages.length])

  // Session management is now handled by parent (HomePage)
  // Just use the prop value directly

  useEffect(() => {
    // Check Weaviate and Curation DB connection status
    const checkHealth = async () => {
      try {
        const response = await fetch('/health/deep')
        if (!response.ok) {
          throw new Error(`Health check failed with status ${response.status}`)
        }
        const data = await response.json()
        setWeaviateConnected(data?.services?.weaviate === 'connected')
        setShowCurationDbWarning(shouldShowCurationDbWarning(data?.services?.curation_db))
      } catch {
        setWeaviateConnected(false)
        setShowCurationDbWarning(true)
      }
    }
    checkHealth()

    // Check conversation status
    const checkConversationStatus = async () => {
      try {
        const response = await fetch('/api/chat/conversation')
        if (response.ok) {
          const data = await response.json()
          setConversationStatus(data)
        }
      } catch (error) {
        console.warn('Failed to fetch conversation status', error)
      }
    }
    checkConversationStatus()

    // Check every 30 seconds
    const interval = setInterval(() => {
      checkHealth()
      checkConversationStatus()
    }, 30000)

    const fetchActiveDocument = async () => {
      debug.log('[Chat] fetchActiveDocument called')
      try {
        const response = await fetch('/api/chat/document')
        if (!response.ok) {
          console.error('[Chat] fetchActiveDocument failed:', response.status)
          throw new Error('Failed to fetch active document')
        }
        const payload = await response.json()
        debug.log('[Chat] fetchActiveDocument response:', payload)

        if (payload?.active && payload.document) {
          debug.log('[Chat] fetchActiveDocument: Found active document:', payload.document.filename)
          setActiveDocument(payload.document)
          localStorage.setItem('chat-active-document', JSON.stringify(payload.document))

          // Load the PDF in the viewer as well
          try {
            const documentId = payload.document.id
            const [detailResponse, urlResponse] = await Promise.all([
              fetch(`/api/pdf-viewer/documents/${documentId}`),
              fetch(`/api/pdf-viewer/documents/${documentId}/url`)
            ])

            if (detailResponse.ok && urlResponse.ok) {
              const detail = await detailResponse.json()
              const urlData = await urlResponse.json()
              const viewerUrl = urlData.viewer_url

              if (viewerUrl && detail) {
                debug.log('[PDF RESTORE] Restoring active document to PDF viewer:', payload.document.filename)
                dispatchPDFDocumentChanged(
                  documentId,
                  viewerUrl,
                  detail.filename ?? payload.document.filename ?? 'Untitled',
                  detail.page_count ?? detail.pageCount ?? 1
                )
              }
            } else {
              console.warn('[PDF RESTORE] Failed to fetch PDF metadata for active document')
            }
          } catch (pdfError) {
            console.warn('[PDF RESTORE] Unable to load PDF viewer for active document:', pdfError)
          }
        } else {
          debug.log('[Chat] fetchActiveDocument: No active document from backend')

          // CRITICAL: Check if the event handler has already set a document in localStorage
          // This prevents a race condition where fetchActiveDocument() completes after
          // the user loads a document from DocumentsPage
          const localDoc = localStorage.getItem('chat-active-document')
          if (localDoc) {
            debug.log('[Chat] fetchActiveDocument: But localStorage has a document, not clearing (event handler won)')
          } else {
            debug.log('[Chat] fetchActiveDocument: No document in localStorage either, clearing state')
            setActiveDocument(null)
            localStorage.removeItem('chat-active-document')
          }
        }
      } catch (error) {
        console.error('[Chat] fetchActiveDocument error:', error)
      }
    }

    debug.log('[Chat] Calling fetchActiveDocument on mount')
    fetchActiveDocument()

    const documentChangeHandler = async (event: Event) => {
      debug.log('[Chat] chat-document-changed event received', event)
      const customEvent = event as CustomEvent
      const detail = customEvent.detail || {}
      debug.log('[Chat] Event detail:', detail)

      if (detail?.active && detail.document) {
        debug.log('[Chat] Setting active document:', detail.document.filename || detail.document.id)
        setActiveDocument(detail.document)
        localStorage.setItem('chat-active-document', JSON.stringify(detail.document))

        // Reset chat when loading a new document - old conversation context is no longer relevant
        debug.log('[Chat] Resetting chat for new document')
        try {
          const resetResponse = await fetch('/api/chat/conversation/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          })
          if (resetResponse.ok) {
            const resetData = await resetResponse.json()
            debug.log('[Chat] Conversation reset for new document:', resetData)
            // Propagate new session ID
            if (resetData.session_id && onSessionChange) {
              onSessionChange(resetData.session_id)
            }
            // Clear messages from UI and localStorage
            latestMessagesRef.current = []
            setMessages([])
            localStorage.removeItem(CHAT_MESSAGES_KEY)
            sessionTraceIds.current = []
            dispatchClearHighlights('document-change')
          }
        } catch (resetError) {
          console.error('[Chat] Failed to reset conversation for new document:', resetError)
        }

        // Load the PDF in the viewer when document changes
        try {
          const documentId = detail.document.id
          debug.log('[Chat] Fetching PDF metadata for:', documentId)
          const [detailResponse, urlResponse] = await Promise.all([
            fetch(`/api/pdf-viewer/documents/${documentId}`),
            fetch(`/api/pdf-viewer/documents/${documentId}/url`)
          ])

          if (detailResponse.ok && urlResponse.ok) {
            const pdfDetail = await detailResponse.json()
            const urlData = await urlResponse.json()
            const viewerUrl = urlData.viewer_url

            if (viewerUrl && pdfDetail) {
              debug.log('[Chat] Loading PDF in viewer after document change:', detail.document.filename)
              dispatchPDFDocumentChanged(
                documentId,
                viewerUrl,
                pdfDetail.filename ?? detail.document.filename ?? 'Untitled',
                pdfDetail.page_count ?? pdfDetail.pageCount ?? 1
              )
            }
          } else {
            console.warn('[Chat] Failed to fetch PDF metadata for document change')
          }
        } catch (pdfError) {
          console.warn('[Chat] Unable to load PDF viewer after document change:', pdfError)
        }
      } else {
        debug.log('[Chat] Clearing active document')
        setActiveDocument(null)
        localStorage.removeItem('chat-active-document')
      }
    }

    debug.log('[Chat] Setting up chat-document-changed event listener')
    window.addEventListener('chat-document-changed', documentChangeHandler)

    return () => {
      window.removeEventListener('chat-document-changed', documentChangeHandler)
      clearInterval(interval)
    }
  }, [onSessionChange])

  const handleCopyMessage = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      // Optional: You could show a toast notification here
      debug.log('Message copied to clipboard')
    }).catch(err => {
      console.error('Failed to copy:', err)
    })
  }

  const handleFeedbackClick = (messageContent: string, messageTraceIds?: string[]) => {
    // Use specific message trace IDs if available, otherwise fallback to session IDs
    const traceIdsToUse = (messageTraceIds && messageTraceIds.length > 0) 
      ? messageTraceIds 
      : sessionTraceIds.current

    debug.log('🔍 [FEEDBACK] Submitting feedback with trace IDs:', traceIdsToUse)
    setFeedbackMessageData({
      content: messageContent,
      traceIds: traceIdsToUse
    })
    setFeedbackDialogOpen(true)
  }

  const handleFeedbackDialogClose = () => {
    setFeedbackDialogOpen(false)
    setFeedbackMessageData(null)
  }

  const handleFeedbackSubmit = async (feedback: {
    session_id: string
    curator_id: string
    feedback_text: string
    trace_ids: string[]
  }) => {
    try {
      await submitFeedback(feedback)
      debug.log('Feedback submitted successfully')
    } catch (error) {
      console.error('Failed to submit feedback:', error)
      throw error // Re-throw to let FeedbackDialog handle the error display
    }
  }

  const handleOpenCurationWorkspace = useCallback(
    async (
      target: CurationWorkspaceLaunchTarget,
      options?: { messageId?: string }
    ) => {
      try {
        const sessionId = await openCurationWorkspace({
          ...target,
          navigate,
        })

        if (options?.messageId) {
          const messageId = options.messageId
          setMessages(prev => withUpdatedReviewAndCurateSessionId(prev, messageId, sessionId))
        }

        return sessionId
      } catch (error) {
        emitGlobalToast({
          message: error instanceof Error ? error.message : 'Failed to open the curation workspace.',
          severity: 'error',
        })
        return null
      }
    },
    [navigate]
  )

  const handleOpenPrepDialog = async () => {
    if (!propSessionId) {
      setPrepStatus({
        kind: 'error',
        message: 'Start a chat session before preparing for curation.',
      })
      return
    }

    setPrepDialogOpen(true)
    setPrepDialogError(null)
    setPrepPreview(null)
    setIsLoadingPrepPreview(true)

    try {
      const preview = await fetchCurationPrepPreview(propSessionId)
      setPrepPreview(preview)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to load curation scope.'
      setPrepDialogError(message)
    } finally {
      setIsLoadingPrepPreview(false)
    }
  }

  const handleClosePrepDialog = () => {
    if (isPreparingCuration) {
      return
    }

    setPrepDialogOpen(false)
    setPrepDialogError(null)
    setPrepPreview(null)
    setIsLoadingPrepPreview(false)
  }

  const handleConfirmPrep = useCallback(async () => {
    if (!propSessionId || !prepPreview) {
      setPrepDialogError('Curation scope is not available yet.')
      return
    }

    setIsPreparingCuration(true)
    setPrepDialogError(null)
    setPrepStatus({
      kind: 'info',
      message: 'Preparing candidate annotations for curation review...',
    })

    try {
      const result = await runCurationPrep({
        session_id: propSessionId,
        adapter_keys: prepPreview.adapter_keys,
      })

      const warningText = result.warnings.length > 0
        ? ` Warnings: ${result.warnings.join(' ')}`
        : ''
      const prepSummary = `${result.summary_text}${warningText}`.trim()
      const targetDocumentId = result.document_id || activeDocument?.id || null
      const reviewAndCurateTarget = targetDocumentId
        ? {
            documentId: targetDocumentId,
            originSessionId: propSessionId,
            adapterKeys: result.adapter_keys,
          }
        : null
      const prepMessageId = `prep-${Date.now()}`

      setPrepStatus(null)
      setPrepDialogOpen(false)
      setPrepPreview(null)
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: prepSummary,
          timestamp: new Date(),
          id: prepMessageId,
          reviewAndCurateTarget,
        },
      ])

      if (reviewAndCurateTarget) {
        void handleOpenCurationWorkspace(reviewAndCurateTarget, { messageId: prepMessageId })
      } else {
        emitGlobalToast({
          message: 'Curation prep completed, but there is no active document to review.',
          severity: 'warning',
        })
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to prepare curation scope.'
      setPrepDialogError(message)
      setPrepStatus({
        kind: 'error',
        message,
      })
    } finally {
      setIsPreparingCuration(false)
    }
  }, [activeDocument?.id, handleOpenCurationWorkspace, prepPreview, propSessionId])

  const handleResetConversation = async () => {
    if (!window.confirm('Are you sure you want to reset the chat? This will clear all messages and conversation memory.')) {
      return
    }

    setIsResetting(true)
    setLimitNotices([])
    try {
      const response = await fetch('/api/chat/conversation/reset', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      })

      if (response.ok) {
        const data = await response.json()
        debug.log('Conversation reset:', data)

        // Clear persisted audit history for the old/new session IDs so refresh stays clean.
        if (propSessionId) {
          localStorage.removeItem(`audit_events_${propSessionId}`)
        }
        if (data.session_id) {
          localStorage.removeItem(`audit_events_${data.session_id}`)
        }

        // Propagate new session ID back to HomePage if reset created a new session
        if (data.session_id && onSessionChange) {
          debug.log('🔄 [Session Reset] Propagating new session ID to HomePage:', data.session_id)
          onSessionChange(data.session_id)
        }

        // Clear messages from UI and localStorage
        latestMessagesRef.current = []
        setMessages([])
        localStorage.removeItem(CHAT_MESSAGES_KEY)
        sessionTraceIds.current = [] // Clear accumulated trace IDs for new session
        dispatchClearHighlights('user-action')
        // Update conversation status
        const statusResponse = await fetch('/api/chat/conversation')
        if (statusResponse.ok) {
          const statusData = await statusResponse.json()
          setConversationStatus(statusData)
        }
      } else {
        console.error('Failed to reset conversation')
        alert('Failed to reset chat. Please try again.')
      }
    } catch (error) {
      console.error('Error resetting conversation:', error)
      alert('An error occurred while resetting the chat.')
    } finally {
      setIsResetting(false)
    }
  }

  const handleUnloadPDF = async () => {
    if (!activeDocument) {
      return
    }

    if (!window.confirm(`Are you sure you want to unload "${activeDocument.filename || 'the active PDF'}"? You can reload it later from the Documents panel.`)) {
      return
    }

    setIsUnloadingPDF(true)
    try {
      const response = await fetch('/api/chat/document', {
        method: 'DELETE',
      })

      if (response.ok) {
        debug.log('PDF unloaded successfully')

        // Clear from local state
        setActiveDocument(null)
        localStorage.removeItem('chat-active-document')

        // Dispatch event to notify other components (like PDF viewer)
        window.dispatchEvent(
          new CustomEvent('chat-document-changed', {
            detail: { active: false, document: null }
          })
        )
      } else {
        console.error('Failed to unload PDF')
        alert('Failed to unload PDF. Please try again.')
      }
    } catch (error) {
      console.error('Error unloading PDF:', error)
      alert('An error occurred while unloading the PDF.')
    } finally {
      setIsUnloadingPDF(false)
    }
  }

  const handleSendMessage = async () => {
    if (!inputMessage.trim()) return
    if (!propSessionId) {
      console.error('No session ID available')
      return
    }

    setLimitNotices([])
    dispatchClearHighlights('new-query')
    assistantMessageIdRef.current = null
    assistantMessageRef.current = '' // Reset assistant message accumulator

    const messageToSend = inputMessage

    const userMessage: Message = {
      role: 'user',
      content: messageToSend,
      timestamp: new Date(),
      id: `msg-${Date.now()}`
    }

    setMessages(prev => [...prev, userMessage])
    setInputMessage('')

    try {
      // Use hook's sendMessage function
      await sendMessage(messageToSend, propSessionId)
      setRefinePrompt(null)
      setRefineText('')
    } catch (err) {
      console.error('Error sending message:', err)
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: 'Sorry, I encountered an error. Please try again.',
          timestamp: new Date(),
          id: `msg-${Date.now()}`
        }
      ])
    } finally {
      // Clear progress state
      setProgressMessage('')
      if (progressMessageTimerRef.current) {
        clearTimeout(progressMessageTimerRef.current)
        progressMessageTimerRef.current = null
      }
      progressMessageQueueRef.current = []
      lastProgressUpdateRef.current = 0
    }
  }

  const handleSendQuickMessage = async (text: string) => {
    if (!text.trim()) return
    if (!propSessionId) {
      console.error('No session ID available')
      return
    }

    setLimitNotices([])
    dispatchClearHighlights('new-query')
    assistantMessageIdRef.current = null
    assistantMessageRef.current = '' // Reset assistant message accumulator

    const userMessage: Message = {
      role: 'user',
      content: text,
      timestamp: new Date(),
      id: `msg-${Date.now()}`
    }

    setMessages(prev => [...prev, userMessage])

    try {
      await sendMessage(text, propSessionId)
      setRefinePrompt(null)
    } catch (err) {
      console.error('Error sending quick message:', err)
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: 'Sorry, I encountered an error. Please try again.',
          timestamp: new Date(),
          id: `msg-${Date.now()}`
        }
      ])
    } finally {
      setProgressMessage('')
      if (progressMessageTimerRef.current) {
        clearTimeout(progressMessageTimerRef.current)
        progressMessageTimerRef.current = null
      }
      progressMessageQueueRef.current = []
      lastProgressUpdateRef.current = 0
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSendMessage()
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value
    setInputMessage(value)

    // Auto-resize the textarea to fit content up to the max-height
    if (textareaRef.current) {
      const el = textareaRef.current
      el.style.height = 'auto'
      const maxHeight = 120 // match CSS max-height
      const nextHeight = Math.min(el.scrollHeight, maxHeight)
      el.style.height = `${nextHeight}px`
      el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
    }
  }

  const handleRefineSubmit = async () => {
    if (!refineText.trim()) return
    await handleSendQuickMessage(refineText)
    setRefineText('')
  }

  const prepButtonLabel = isPreparingCuration
    ? 'Preparing...'
    : isLoadingPrepPreview
      ? 'Loading Scope...'
      : 'Prepare for Curation'

  const prepStatusStyle = prepStatus?.kind === 'success'
    ? {
        border: '1px solid rgba(76, 175, 80, 0.35)',
        background: 'rgba(76, 175, 80, 0.08)',
        color: '#2e7d32',
      }
    : prepStatus?.kind === 'error'
      ? {
          border: '1px solid rgba(220, 53, 69, 0.35)',
          background: 'rgba(220, 53, 69, 0.08)',
          color: '#c12d3c',
        }
      : {
          border: '1px solid rgba(33, 150, 243, 0.35)',
          background: 'rgba(33, 150, 243, 0.08)',
          color: '#0d6efd',
        }

  return (
    <div
      style={{
        height: '100%',
        flex: '1 1 auto',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        backgroundColor: 'transparent',
        overflow: 'hidden',
      }}
    >
      <div className="chat-header">
        <h2>AI Assistant Chat</h2>
        <div className="chat-status">
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            {activeDocument ? (
              <span>
                Active PDF: {activeDocument.filename || activeDocument.id}
              </span>
            ) : (
              <span>No PDF loaded</span>
            )}

            {conversationStatus && (
              <span style={{ fontSize: '0.9em', color: '#666' }}>
                Memory: {
                  conversationStatus.memory_stats?.memory_sizes?.short_term?.file_count || 0
                } items
              </span>
            )}

            <button
              onClick={handleResetConversation}
              disabled={isResetting}
              style={{
                padding: '4px 12px',
                backgroundColor: isResetting ? '#ccc' : '#dc3545',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: isResetting ? 'not-allowed' : 'pointer',
                fontSize: '0.9em',
                display: 'flex',
                alignItems: 'center',
                gap: '4px'
              }}
              title="Reset chat and clear all messages"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
              </svg>
              {isResetting ? 'Resetting...' : 'Reset Chat'}
            </button>

            {activeDocument && (
              <button
                onClick={handleUnloadPDF}
                disabled={isUnloadingPDF}
                style={{
                  padding: '4px 12px',
                  backgroundColor: isUnloadingPDF ? '#ccc' : '#6c757d',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: isUnloadingPDF ? 'not-allowed' : 'pointer',
                  fontSize: '0.9em',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px'
                }}
                title="Unload the active PDF"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                </svg>
                {isUnloadingPDF ? 'Unloading...' : 'Unload PDF'}
              </button>
            )}

            <button
              onClick={handleOpenPrepDialog}
              disabled={!propSessionId || isLoadingPrepPreview || isPreparingCuration}
              style={{
                padding: '4px 12px',
                backgroundColor: (isLoadingPrepPreview || isPreparingCuration || !propSessionId)
                  ? '#ccc'
                  : '#2e7d32',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: (isLoadingPrepPreview || isPreparingCuration || !propSessionId)
                  ? 'not-allowed'
                  : 'pointer',
                fontSize: '0.9em',
                display: 'flex',
                alignItems: 'center',
                gap: '4px'
              }}
              title="Prepare the current chat scope for curation review"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-8 14l-5-5 1.41-1.41L11 14.17l5.59-5.58L18 10l-7 7z"/>
              </svg>
              {prepButtonLabel}
            </button>
          </div>
        </div>
      </div>

      {prepStatus && (
        <div
          role={prepStatus.kind === 'error' ? 'alert' : 'status'}
          style={{
            margin: '8px 0',
            padding: '8px 12px',
            borderRadius: '6px',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            ...prepStatusStyle,
          }}
        >
          <span>{prepStatus.message}</span>
        </div>
      )}

      {limitNotices.length > 0 && (
        <div
          style={{
            margin: '8px 0',
            padding: '8px 12px',
            border: '1px solid rgba(33, 150, 243, 0.35)',
            borderRadius: '6px',
            background: 'rgba(33, 150, 243, 0.08)',
            color: '#0d6efd',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
          }}
        >
          {limitNotices.map((n, idx) => (
            <span key={idx}>{n}</span>
          ))}
        </div>
      )}

      {refinePrompt && (
        <div
          style={{
            margin: '8px 0',
            padding: '8px 12px',
            border: '1px solid rgba(220, 53, 69, 0.4)',
            borderRadius: '6px',
            background: 'rgba(220, 53, 69, 0.08)',
            color: '#c12d3c',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
          }}
        >
          <span>{refinePrompt}</span>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text"
              value={refineText}
              onChange={e => setRefineText(e.target.value)}
              placeholder="e.g., Use limit 50 for mouse (MGI)"
              style={{
                padding: '6px 8px',
                borderRadius: '4px',
                border: '1px solid rgba(0,0,0,0.15)',
                minWidth: '260px',
              }}
            />
            <button
              onClick={handleRefineSubmit}
              style={{
                padding: '6px 12px',
                borderRadius: '4px',
                border: '1px solid #c12d3c',
                background: '#c12d3c',
                color: 'white',
                cursor: 'pointer'
              }}
            >
              Send
            </button>
            <button
              onClick={() => handleSendQuickMessage('Use limit 50 and add a species/provider filter.')}
              style={{
                padding: '4px 10px',
                borderRadius: '4px',
                border: '1px solid #c12d3c',
                background: '#c12d3c',
                color: 'white',
                cursor: 'pointer'
              }}
            >
              Proceed with limit 50
            </button>
            <button
              onClick={() => handleSendQuickMessage('Use limit 100 and add a species/provider filter.')}
              style={{
                padding: '4px 10px',
                borderRadius: '4px',
                border: '1px solid #c12d3c',
                background: 'transparent',
                color: '#c12d3c',
                cursor: 'pointer'
              }}
            >
              Proceed with limit 100
            </button>
            <button
              onClick={() => setRefinePrompt(null)}
              style={{
                padding: '4px 10px',
                borderRadius: '4px',
                border: '1px solid rgba(0,0,0,0.1)',
                background: 'transparent',
                color: '#666',
                cursor: 'pointer'
              }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {!weaviateConnected && (
        <div className="weaviate-warning">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: '8px' }}>
            <path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>
          </svg>
          Weaviate database connection lost - PDF search unavailable
        </div>
      )}

      {showCurationDbWarning && (
        <div className="weaviate-warning">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: '8px' }}>
            <path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>
          </svg>
          Curation database connection lost - all database queries unavailable
        </div>
      )}


      <div data-testid="messages-container" style={{
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '1.5rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '1.5rem',
        backgroundColor: 'transparent',
        scrollBehavior: 'smooth',
        borderTop: '1px solid rgba(255, 255, 255, 0.08)',
        borderBottom: '1px solid rgba(255, 255, 255, 0.08)'
      }}>
        {messages.length === 0 ? (
          <div className="empty-state">
            Ask a question to get started...
          </div>
        ) : (
          messages.map((message, index) => {
            const hasEvidenceCard = (message.evidenceRecords?.length ?? 0) > 0

            if (message.role === 'assistant') {
              return (
                <div
                  key={message.id || index}
                  style={{
                    alignSelf: 'flex-start',
                    maxWidth: '85%',
                    display: 'flex',
                    flexDirection: 'column',
                    minWidth: 0,
                  }}
                >
                  <div
                    className="message assistant-message"
                    style={{
                      maxWidth: '100%',
                      borderRadius: hasEvidenceCard ? '18px 18px 4px 4px' : undefined,
                    }}
                  >
                    <div className="message-role">
                      AI Assistant
                    </div>
                    <div className="message-content">
                      {message.type === 'file_download' && message.fileData ? (
                        <FileDownloadCard file={message.fileData} />
                      ) : (
                        message.content
                      )}
                    </div>
                    <MessageActions
                      messageContent={message.content}
                      traceId={message.traceIds && message.traceIds.length > 0 ? message.traceIds[message.traceIds.length - 1] : undefined}
                      onFeedbackClick={() => handleFeedbackClick(message.content, message.traceIds)}
                      reviewAndCurateTarget={message.reviewAndCurateTarget}
                      onReviewAndCurateOpened={(sessionId) => {
                        const messageId = message.id
                        if (!messageId) {
                          return
                        }

                        setMessages(prev => withUpdatedReviewAndCurateSessionId(prev, messageId, sessionId))
                      }}
                    />
                  </div>

                  {hasEvidenceCard ? (
                    <EvidenceCard
                      evidenceRecords={message.evidenceRecords ?? []}
                      reviewAndCurateTarget={message.reviewAndCurateTarget}
                      onReviewAndCurateClick={message.reviewAndCurateTarget ? () => {
                        const messageId = message.id
                        void handleOpenCurationWorkspace(
                          message.reviewAndCurateTarget!,
                          messageId ? { messageId } : undefined,
                        )
                      } : null}
                    />
                  ) : null}
                </div>
              )
            }

            return (
              <div
                key={message.id || index}
                className="message user-message"
              >
                <div className="message-role">
                  You
                </div>
                <div className="message-content">
                  {message.content}
                </div>
                <button
                  className="copy-button"
                  onClick={() => handleCopyMessage(message.content)}
                  title="Copy to clipboard"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>
                  </svg>
                </button>
              </div>
            )
          })
        )}
        {isLoading && (
          <div className="loading-indicator">
            <span>{progressMessage || 'AI is thinking...'}</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="input-container">
        <textarea
          ref={textareaRef}
          className="message-input"
          placeholder="Type your message..."
          value={inputMessage}
          onChange={handleInputChange}
          onKeyPress={handleKeyPress}
          rows={1}
        />
        <button
          className="send-button"
          onClick={handleSendMessage}
          disabled={isLoading || !inputMessage.trim()}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
          </svg>
        </button>
      </div>

      {/* Feedback Dialog */}
      <FeedbackDialog
        open={feedbackDialogOpen}
        onClose={handleFeedbackDialogClose}
        sessionId={propSessionId}
        traceIds={feedbackMessageData?.traceIds || []}
        curatorId={user?.email || 'unknown@example.com'}
        onSubmit={handleFeedbackSubmit}
      />

      <PrepScopeConfirmationDialog
        open={prepDialogOpen}
        preview={prepPreview}
        loading={isLoadingPrepPreview}
        submitting={isPreparingCuration}
        error={prepDialogError}
        onClose={handleClosePrepDialog}
        onConfirm={handleConfirmPrep}
      />
    </div>
  )
}

export default Chat
