import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import type React from 'react'
import { useNavigate } from 'react-router-dom'

import { debug } from '@/utils/env'
import {
  HOME_PDF_VIEWER_OWNER,
  dispatchClearHighlights,
} from '@/components/pdfViewer/pdfEvents'
import { copyText } from '@/components/Chat/copyText'
import {
  fetchCurationPrepPreview,
  runCurationPrep,
  type CurationPrepPreview,
} from '@/features/curation/services/curationPrepService'
import { readCurationApiError } from '@/features/curation/services/api'
import {
  openCurationWorkspace,
  type CurationWorkspaceLaunchTarget,
} from '@/features/curation/navigation/openCurationWorkspace'
import { rehydrateChatDocumentFromSource } from '@/features/documents/chatDocumentRehydration'
import { submitFeedback } from '@/services/feedbackService'
import { useAuth } from '@/contexts/AuthContext'
import type { SSEEvent } from '@/hooks/useChatStream'
import { emitGlobalToast } from '@/lib/globalNotifications'
import { normalizeOptionalText } from '@/lib/normalizeOptionalText'
import { getStreamEventSessionId } from '@/lib/streamEventSession'
import { clearChatRenderCacheForSession, getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import { extractEvidenceCurationSupport } from '@/services/chatHistoryApi'

import {
  MIXED_CURATION_PREP_WARNING_MESSAGE,
  UNSUPPORTED_CURATION_REVIEW_MESSAGE,
} from './constants'
import { getFriendlyProgressMessage, shouldShowInChat } from './chatProgress'
import {
  buildEvidenceReviewAndCurateTarget,
  buildTurnId,
  buildUserTurnMessageId,
  extractEventTimestamp,
  extractEvidenceRecords,
  extractFlowStepEvidenceDetails,
  findAssistantMessageIndex,
  getEventTurnId,
  getTerminalTurnDefaultMessage,
  humanizeAdapterKey,
  loadMessagesFromStorage,
  mergeTraceIds,
  shouldShowCurationDbWarning,
  upsertAssistantTurnMessage,
  withEvidenceRecords,
  withFlowStepEvidenceMessage,
  withMissingEvidenceReviewAndCurateTargets,
  withTraceIdOnAssistantTurn,
  withUpdatedReviewAndCurateSessionId,
} from './chatMessageUtils'
import type {
  ActiveDocument,
  ChatProps,
  ConversationStatus,
  Message,
  PrepStatus,
  SerializedMessage,
  StoredChatData,
} from './types'
import type { FileInfo } from './FileDownloadCard'

export function useChatController({
  sessionId: propSessionId,
  onSessionChange,
  events,
  isLoading,
  sendMessage
}: ChatProps) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const feedbackSessionId = typeof propSessionId === 'string' && propSessionId.trim().length > 0
    ? propSessionId.trim()
    : null
  const storageUserId = user?.uid ?? null
  const chatStorageKeys = useMemo(
    () => (storageUserId ? getChatLocalStorageKeys(storageUserId) : null),
    [storageUserId],
  )
  // Initialize messages from localStorage if available
  const [messages, setMessages] = useState<Message[]>(() => loadMessagesFromStorage(chatStorageKeys, propSessionId))
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
  const [prepStatus, setPrepStatus] = useState<PrepStatus | null>(null)
  const [refinePrompt, setRefinePrompt] = useState<string | null>(null)
  const [refineText, setRefineText] = useState<string>('')
  const [limitNotices, setLimitNotices] = useState<string[]>([])
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const progressMessageQueueRef = useRef<string[]>([])
  const progressMessageTimerRef = useRef<NodeJS.Timeout | null>(null)
  const sessionIdCopyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastProgressUpdateRef = useRef<number>(0)
  const assistantBuffersRef = useRef<Record<string, string>>({})
  const activeTurnIdRef = useRef<string | null>(null)
  const rescuedTurnIdsRef = useRef<Set<string>>(new Set())
  const processedEventIdsRef = useRef<Set<number>>(new Set())
  const latestMessagesRef = useRef<Message[]>(messages)
  const latestSessionIdRef = useRef<string | null>(propSessionId)
  const sessionStateVersionRef = useRef(0)
  const persistTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const restoredSessionRef = useRef<string | null>(null)
  const messageStorageUserIdRef = useRef<string | null>(storageUserId)
  const storageUserIdRef = useRef<string | null>(storageUserId)
  const previousSessionIdRef = useRef<string | null>(propSessionId)
  const normalizedSessionId = normalizeOptionalText(propSessionId)
  const [sessionIdCopied, setSessionIdCopied] = useState(false)

  // Track ALL trace IDs from this session for feedback
  const sessionTraceIds = useRef<string[]>([])

  // Keep "latest" refs synchronized during render to avoid stale values during unmount cleanup.
  latestMessagesRef.current = messages
  if (messageStorageUserIdRef.current === storageUserId) {
    latestSessionIdRef.current = propSessionId
  }

  const persistMessagesToStorage = useCallback((nextMessages: Message[], sessionId: string | null) => {
    try {
      if (!sessionId || !chatStorageKeys || messageStorageUserIdRef.current !== storageUserId) return

      if (nextMessages.length === 0) {
        localStorage.removeItem(chatStorageKeys.messages)
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
      localStorage.setItem(chatStorageKeys.messages, JSON.stringify(storageData))
    } catch (error) {
      console.warn('Failed to persist messages to localStorage:', error)
    }
  }, [chatStorageKeys, storageUserId])

  const getStoredActiveDocument = useCallback(() => {
    if (!chatStorageKeys) {
      return null
    }
    return localStorage.getItem(chatStorageKeys.activeDocument)
  }, [chatStorageKeys])

  const clearStoredActiveDocument = useCallback(() => {
    if (!chatStorageKeys) {
      return
    }
    localStorage.removeItem(chatStorageKeys.activeDocument)
  }, [chatStorageKeys])

  const clearStoredMessages = useCallback(() => {
    if (!chatStorageKeys) {
      return
    }
    localStorage.removeItem(chatStorageKeys.messages)
  }, [chatStorageKeys])

  const restoreDocumentToPdfViewer = useCallback(async (
    document: ActiveDocument,
    shouldCommitViewerRestore?: () => boolean,
  ) => {
    await rehydrateChatDocumentFromSource({
      loadDocument: async () => document,
      chatStorageKeys,
      ownerToken: HOME_PDF_VIEWER_OWNER,
      shouldCommitViewerRestore,
    })
  }, [chatStorageKeys])

  const clearProgressState = useCallback(() => {
    setProgressMessage('')
    if (progressMessageTimerRef.current) {
      clearTimeout(progressMessageTimerRef.current)
      progressMessageTimerRef.current = null
    }
    progressMessageQueueRef.current = []
    lastProgressUpdateRef.current = 0
  }, [])

  const invalidateTurnRuntimeState = useCallback(() => {
    sessionStateVersionRef.current += 1
    assistantBuffersRef.current = {}
    activeTurnIdRef.current = null
    rescuedTurnIdsRef.current = new Set()
  }, [])

  const getAssistantTurnContent = useCallback((turnId: string): string => {
    const bufferedContent = assistantBuffersRef.current[turnId]
    if (bufferedContent) {
      return bufferedContent
    }

    const assistantMessage = latestMessagesRef.current.find(
      (message) => message.role === 'assistant' && message.turnId === turnId,
    )
    return assistantMessage?.content ?? ''
  }, [])

  const handleAssistantRescue = useCallback(async (
    sessionId: string,
    turnId: string,
    traceId?: string | null,
  ) => {
    const rescueSessionVersion = sessionStateVersionRef.current

    if (rescuedTurnIdsRef.current.has(turnId)) {
      return
    }

    rescuedTurnIdsRef.current.add(turnId)
    const assistantContent = getAssistantTurnContent(turnId)

    if (!assistantContent.trim()) {
      if (sessionStateVersionRef.current !== rescueSessionVersion) {
        return
      }
      setMessages((prev) => upsertAssistantTurnMessage(prev, {
        turnId,
        traceId,
        terminalState: 'turn_save_failed',
        terminalMessage: 'The response completed, but it could not be saved to chat history.',
        rescueState: 'failed',
      }))
      return
    }

    try {
      const response = await fetch(`/api/chat/${encodeURIComponent(sessionId)}/assistant-rescue`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          turn_id: turnId,
          content: assistantContent,
          trace_id: traceId ?? undefined,
        }),
      })

      if (!response.ok) {
        throw new Error(await readCurationApiError(response))
      }

      const payload = await response.json() as {
        trace_id?: string | null
      }

      if (sessionStateVersionRef.current !== rescueSessionVersion) {
        return
      }

      setMessages((prev) => upsertAssistantTurnMessage(prev, {
        turnId,
        content: assistantContent,
        traceId: payload.trace_id ?? traceId,
        terminalState: 'turn_completed',
        terminalMessage: null,
        rescueState: null,
      }))
    } catch (error) {
      const errorMessage = error instanceof Error
        ? error.message
        : 'Failed to rescue the assistant response.'
      if (sessionStateVersionRef.current !== rescueSessionVersion) {
        return
      }
      setMessages((prev) => upsertAssistantTurnMessage(prev, {
        turnId,
        content: assistantContent,
        traceId,
        terminalState: 'turn_save_failed',
        terminalMessage: `This response is shown above, but it could not be saved to chat history: ${errorMessage}`,
        rescueState: 'failed',
      }))
    } finally {
      if (sessionStateVersionRef.current !== rescueSessionVersion) {
        return
      }
      delete assistantBuffersRef.current[turnId]
      if (activeTurnIdRef.current === turnId) {
        activeTurnIdRef.current = null
      }
    }
  }, [getAssistantTurnContent])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
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

  useEffect(() => {
    setSessionIdCopied(false)

    if (sessionIdCopyTimeoutRef.current) {
      clearTimeout(sessionIdCopyTimeoutRef.current)
      sessionIdCopyTimeoutRef.current = null
    }
  }, [normalizedSessionId])

  useEffect(() => {
    if (storageUserIdRef.current === storageUserId) {
      return
    }

    storageUserIdRef.current = storageUserId
    messageStorageUserIdRef.current = null
    restoredSessionRef.current = null
    sessionTraceIds.current = []
    latestMessagesRef.current = []
    invalidateTurnRuntimeState()
    progressMessageQueueRef.current = []
    lastProgressUpdateRef.current = 0
    setMessages([])
    setActiveDocument(null)
    setProgressMessage('')

    if (progressMessageTimerRef.current) {
      clearTimeout(progressMessageTimerRef.current)
      progressMessageTimerRef.current = null
    }

    if (persistTimeoutRef.current) {
      clearTimeout(persistTimeoutRef.current)
      persistTimeoutRef.current = null
    }
  }, [invalidateTurnRuntimeState, storageUserId])

  // If session arrives after mount (or changes), restore persisted messages once per session.
  useEffect(() => {
    if (!propSessionId || messages.length > 0 || restoredSessionRef.current === propSessionId) return

    restoredSessionRef.current = propSessionId
    const restored = loadMessagesFromStorage(chatStorageKeys, propSessionId)
    if (restored.length > 0) {
      messageStorageUserIdRef.current = storageUserId
      setMessages(restored)
    }
  }, [chatStorageKeys, propSessionId, messages.length, storageUserId])

  useEffect(() => {
    if (previousSessionIdRef.current === propSessionId) {
      return
    }

    previousSessionIdRef.current = propSessionId
    invalidateTurnRuntimeState()
    clearProgressState()
  }, [clearProgressState, invalidateTurnRuntimeState, propSessionId])

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

  useEffect(() => {
    if (messages.length === 0) {
      messageStorageUserIdRef.current = storageUserId
      latestSessionIdRef.current = propSessionId
    }
  }, [messages.length, propSessionId, storageUserId])

  // Flush latest chat state on unmount so navigation does not lose pending debounced updates.
  useEffect(() => {
    return () => {
      if (sessionIdCopyTimeoutRef.current) {
        clearTimeout(sessionIdCopyTimeoutRef.current)
        sessionIdCopyTimeoutRef.current = null
      }

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
      const eventSessionId = getStreamEventSessionId(parsed as { session_id?: unknown })
      if (eventSessionId && propSessionId && eventSessionId !== propSessionId) {
        debug.log('🔍 [SSE] Ignoring event for stale session:', {
          eventType: parsed.type,
          eventSessionId,
          activeSessionId: propSessionId,
        })
        return
      }

      const turnId = getEventTurnId(parsed) ?? activeTurnIdRef.current
      const messageTimestamp = extractEventTimestamp(parsed) ?? new Date()
      debug.log('🔍 [SSE] Processing event:', parsed.type, parsed)

      // RUN_STARTED
      if (parsed.type === 'RUN_STARTED') {
        if (turnId) {
          activeTurnIdRef.current = turnId
        }
        updateProgressMessage('Starting...')
        // Capture trace_id early so it's available for STOP_CONFIRMED
        if (parsed.trace_id && !sessionTraceIds.current.includes(parsed.trace_id)) {
          debug.log('🔍 [TRACE] Captured trace ID from RUN_STARTED:', parsed.trace_id)
          sessionTraceIds.current.push(parsed.trace_id)
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

        setMessages((prev) => withTraceIdOnAssistantTurn(prev, parsed.trace_id, turnId))
      }

      if (parsed.type === 'STOP_CONFIRMED') {
        const stopMessage =
          normalizeOptionalText(parsed.details?.message)
          ?? 'Interaction stopped by user'
        clearProgressState()

        if (turnId) {
          const content = getAssistantTurnContent(turnId) || stopMessage
          setMessages((prev) => upsertAssistantTurnMessage(prev, {
            turnId,
            content,
            timestamp: messageTimestamp,
            traceId: parsed.trace_id,
            terminalState: 'turn_interrupted',
            terminalMessage: stopMessage,
            rescueState: null,
          }))
          delete assistantBuffersRef.current[turnId]
          if (activeTurnIdRef.current === turnId) {
            activeTurnIdRef.current = null
          }
          return
        }

        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: stopMessage,
            timestamp: messageTimestamp,
            id: `msg-${Date.now()}`,
            traceIds: mergeTraceIds([...sessionTraceIds.current], parsed.trace_id),
            terminalState: 'turn_interrupted',
            terminalMessage: stopMessage,
            rescueState: null,
          },
        ])
        return
      }

      if (parsed.type === 'RUN_ERROR') {
        const runErrorMessage =
          normalizeOptionalText(parsed.message)
          ?? normalizeOptionalText(parsed.error)
          ?? normalizeOptionalText(parsed.details?.message)
          ?? 'The chat run encountered an unexpected error.'
        clearProgressState()

        if (!turnId) {
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content: runErrorMessage,
              timestamp: messageTimestamp,
              id: `msg-${Date.now()}`,
              traceIds: mergeTraceIds([...sessionTraceIds.current], parsed.trace_id),
              terminalState: 'turn_failed',
              terminalMessage: runErrorMessage,
              rescueState: null,
            },
          ])
          return
        }

        setMessages((prev) => {
          const existingIndex = findAssistantMessageIndex(prev, turnId)
          const bufferedContent = normalizeOptionalText(assistantBuffersRef.current[turnId])
            ?? normalizeOptionalText(prev[existingIndex]?.content)
          const content = getAssistantTurnContent(turnId) || bufferedContent || runErrorMessage

          return upsertAssistantTurnMessage(prev, {
            turnId,
            content,
            timestamp: messageTimestamp,
            traceId: parsed.trace_id,
            terminalState: 'turn_failed',
            terminalMessage: runErrorMessage,
            rescueState: null,
          })
        })
        delete assistantBuffersRef.current[turnId]
        if (activeTurnIdRef.current === turnId) {
          activeTurnIdRef.current = null
        }
        return
      }

      if (
        parsed.type === 'turn_completed'
        || parsed.type === 'turn_interrupted'
        || parsed.type === 'turn_failed'
        || parsed.type === 'turn_save_failed'
        || parsed.type === 'session_gone'
      ) {
        clearProgressState()

        if (parsed.type === 'turn_completed') {
          if (!turnId) {
            return
          }

          setMessages((prev) => {
            if (findAssistantMessageIndex(prev, turnId) === -1) {
              return prev
            }

            return upsertAssistantTurnMessage(prev, {
              turnId,
              traceId: parsed.trace_id,
              timestamp: messageTimestamp,
              terminalState: 'turn_completed',
              terminalMessage: null,
              rescueState: null,
            })
          })
          delete assistantBuffersRef.current[turnId]
          if (activeTurnIdRef.current === turnId) {
            activeTurnIdRef.current = null
          }
          return
        }

        const terminalState = parsed.type
        const terminalMessage =
          normalizeOptionalText(parsed.message)
          ?? getTerminalTurnDefaultMessage(terminalState)

        if (!turnId) {
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content: terminalMessage,
              timestamp: messageTimestamp,
              id: `msg-${Date.now()}`,
              traceIds: mergeTraceIds([...sessionTraceIds.current], parsed.trace_id),
              terminalState,
              terminalMessage: terminalMessage,
              rescueState: terminalState === 'turn_save_failed' ? 'failed' : null,
            },
          ])
          return
        }

        const content = getAssistantTurnContent(turnId) || terminalMessage

        if (terminalState === 'turn_save_failed') {
          setMessages((prev) => upsertAssistantTurnMessage(prev, {
            turnId,
            content,
            timestamp: messageTimestamp,
            traceId: parsed.trace_id,
            terminalState: 'turn_save_failed',
            terminalMessage: terminalMessage,
            rescueState: 'pending',
          }))
          const activeSessionId = eventSessionId ?? propSessionId
          if (activeSessionId) {
            void handleAssistantRescue(activeSessionId, turnId, parsed.trace_id)
          } else {
            setMessages((prev) => upsertAssistantTurnMessage(prev, {
              turnId,
              content,
              timestamp: messageTimestamp,
              traceId: parsed.trace_id,
              terminalState: 'turn_save_failed',
              terminalMessage: terminalMessage,
              rescueState: 'failed',
            }))
          }
          return
        }

        setMessages((prev) => upsertAssistantTurnMessage(prev, {
          turnId,
          content,
          timestamp: messageTimestamp,
          traceId: parsed.trace_id,
          terminalState,
          terminalMessage: terminalMessage,
          rescueState: null,
        }))
        delete assistantBuffersRef.current[turnId]
        if (activeTurnIdRef.current === turnId) {
          activeTurnIdRef.current = null
        }
        return
      }

      // T026: Filter audit events for chat progress display
      if (shouldShowInChat(parsed.type)) {
        const friendlyMessage = getFriendlyProgressMessage(parsed)
        debug.log('🔍 [AUDIT→CHAT] Showing filtered audit event in chat progress:', parsed.type, friendlyMessage)
        updateProgressMessage(friendlyMessage)

        if (
          parsed.details?.reason === 'bulk_guardrail'
          && (parsed.type === 'DOMAIN_SKIPPED' || parsed.type === 'PENDING_USER_INPUT' || parsed.type === 'DOMAIN_WARNING')
        ) {
          setRefinePrompt(parsed.details?.message || 'Please provide a limit or species filter to continue.')
        }

        const appliedLimit = parsed.details?.applied_limit
        const warnings = parsed.details?.warnings
        if (appliedLimit || warnings) {
          const warningText = Array.isArray(warnings) ? warnings.join('; ') : (warnings || '')
          const notice = `Applied limit: ${appliedLimit ?? 'n/a'}${warningText ? ` | Warnings: ${warningText}` : ''}`
          setLimitNotices((prev) => prev.includes(notice) ? prev : [...prev, notice])
        }

        return
      }

      if (parsed.type === 'PROGRESS') {
        debug.log('🔍 [PROGRESS] Received progress event:', parsed.message)
        updateProgressMessage(parsed.message || 'Processing...')
        return
      }

      if (parsed.type === 'CHUNK_PROVENANCE') {
        debug.log('🔍 [CHAT DEBUG] Ignoring legacy CHUNK_PROVENANCE overlay event', {
          chunk_id: parsed.chunk_id,
          document_id: parsed.document_id,
          active_document_id: activeDocument?.id,
        })
        return
      }

      // TEXT_MESSAGE_CONTENT
      const messageContent = parsed.content || parsed.delta
      if (messageContent && parsed.type === 'TEXT_MESSAGE_CONTENT') {
        if (turnId) {
          activeTurnIdRef.current = turnId
          const previousContent = assistantBuffersRef.current[turnId] ?? getAssistantTurnContent(turnId)
          const nextContent = `${previousContent}${messageContent}`
          assistantBuffersRef.current[turnId] = nextContent

          setMessages((prev) => upsertAssistantTurnMessage(prev, {
            turnId,
            content: nextContent,
            timestamp: messageTimestamp,
            traceId: parsed.trace_id,
            terminalState: null,
            terminalMessage: null,
            rescueState: null,
          }))
          return
        }

        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1]
          if (lastMsg && lastMsg.role === 'assistant') {
            return [
              ...prev.slice(0, -1),
              { ...lastMsg, content: `${lastMsg.content}${messageContent}` },
            ]
          }

          return [
            ...prev,
            {
              role: 'assistant',
              content: messageContent,
              timestamp: messageTimestamp,
              id: `msg-${Date.now()}`,
              traceIds: mergeTraceIds([], parsed.trace_id),
            },
          ]
        })
        return
      }

      // CHAT_OUTPUT_READY - flow chat output is finalized in a tool call
      // Emit an assistant message so the user sees the actual flow result text.
      if (parsed.type === 'CHAT_OUTPUT_READY' && parsed.details) {
        const outputText = String(parsed.details.output || parsed.details.output_preview || '').trim()
        if (outputText) {
          if (turnId) {
            assistantBuffersRef.current[turnId] = outputText
            setMessages((prev) => upsertAssistantTurnMessage(prev, {
              turnId,
              content: outputText,
              timestamp: messageTimestamp,
              traceId: parsed.trace_id,
              terminalState: null,
              terminalMessage: null,
              rescueState: null,
            }))
          } else {
            setMessages((prev) => {
              const lastMsg = prev[prev.length - 1]
              if (lastMsg?.role === 'assistant' && lastMsg.content.trim() === outputText) {
                return prev
              }
              return [
                ...prev,
                {
                  role: 'assistant',
                  content: outputText,
                  timestamp: messageTimestamp,
                  id: `msg-${Date.now()}`,
                  traceIds: [...sessionTraceIds.current],
                },
              ]
            })
          }
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
            timestamp: messageTimestamp,
            id: `file-${Date.now()}`,
            turnId: turnId ?? undefined,
            type: 'file_download',
            fileData,
            traceIds: mergeTraceIds([...sessionTraceIds.current], parsed.trace_id),
          },
        ])
        return
      }

      if (parsed.type === 'FLOW_STEP_EVIDENCE') {
        const flowStepEvidence = extractFlowStepEvidenceDetails(parsed)
        if (!flowStepEvidence) {
          console.warn('[Chat] Ignoring malformed FLOW_STEP_EVIDENCE event payload', parsed)
          return
        }

        const messageTimestamp = extractEventTimestamp(parsed)
        if (!messageTimestamp) {
          console.warn('[Chat] Ignoring FLOW_STEP_EVIDENCE event without a valid timestamp', parsed)
          return
        }

        setMessages(prev => withFlowStepEvidenceMessage(
          prev,
          flowStepEvidence,
          messageTimestamp,
        ))
        return
      }

      if (parsed.type === 'evidence_summary') {
        const evidenceRecords = extractEvidenceRecords(parsed.evidence_records)
        if (evidenceRecords.length === 0) {
          return
        }

        const curationSupport = extractEvidenceCurationSupport(parsed)
        const reviewAndCurateTarget = curationSupport?.supported
          ? buildEvidenceReviewAndCurateTarget(
              activeDocument?.id,
              propSessionId,
              curationSupport.adapterKey ? [curationSupport.adapterKey] : undefined,
            )
          : null

        setMessages(prev => withEvidenceRecords(prev, evidenceRecords, {
          turnId,
          reviewAndCurateTarget,
          evidenceCurationSupported: curationSupport?.supported ?? null,
          evidenceCurationAdapterKey: curationSupport?.adapterKey ?? null,
        }))
      }
    })

    // Mark all new events as processed
    processedEventIdsRef.current = new Set(Array.from({ length: events.length }, (_, i) => i))
  }, [
    events,
    activeDocument,
    clearProgressState,
    getAssistantTurnContent,
    handleAssistantRescue,
    propSessionId,
    updateProgressMessage,
  ])

  useEffect(() => {
    if (!activeDocument?.id || !propSessionId) {
      return
    }

    setMessages(prev => withMissingEvidenceReviewAndCurateTargets(
      prev,
      activeDocument.id,
      propSessionId,
    ))
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
    let isActive = true

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
        await rehydrateChatDocumentFromSource({
          loadDocument: async () => {
            const response = await fetch('/api/chat/document')
            if (!response.ok) {
              console.error('[Chat] fetchActiveDocument failed:', response.status)
              throw new Error('Failed to fetch active document')
            }
            const payload = await response.json()
            debug.log('[Chat] fetchActiveDocument response:', payload)

            if (payload?.active && payload.document) {
              debug.log('[Chat] fetchActiveDocument: Found active document:', payload.document.filename)
              return payload.document as ActiveDocument
            }

            debug.log('[Chat] fetchActiveDocument: No active document from backend')
            return null
          },
          chatStorageKeys,
          ownerToken: HOME_PDF_VIEWER_OWNER,
          shouldCommitViewerRestore: () => isActive,
          onDocument: async (activeDocument) => {
            if (!isActive) {
              return false
            }

            debug.log(
              '[Chat] Setting active document:',
              activeDocument.filename || activeDocument.id,
            )
            setActiveDocument(activeDocument)
            debug.log('[PDF RESTORE] Restoring active document to PDF viewer:', activeDocument.filename)
          },
          onMissingDocument: async () => {
            // CRITICAL: Check if the event handler has already set a document in localStorage
            // This prevents a race condition where fetchActiveDocument() completes after
            // the user loads a document from DocumentsPage
            const localDoc = getStoredActiveDocument()
            if (localDoc) {
              debug.log('[Chat] fetchActiveDocument: But localStorage has a document, not clearing (event handler won)')
              return
            }

            if (!isActive) {
              return
            }

            debug.log('[Chat] fetchActiveDocument: No document in localStorage either, clearing state')
            setActiveDocument(null)
            clearStoredActiveDocument()
          },
        })
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
        if (!isActive) {
          return
        }
        debug.log('[Chat] Setting active document:', detail.document.filename || detail.document.id)
        setActiveDocument(detail.document)

        // Reset chat when loading a new document - old conversation context is no longer relevant
        debug.log('[Chat] Resetting chat for new document')
        try {
          const resetResponse = await fetch('/api/chat/conversation/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          })
          if (resetResponse.ok) {
            const resetData = await resetResponse.json()
            if (!isActive) {
              return
            }
            debug.log('[Chat] Conversation reset for new document:', resetData)
            // Propagate new session ID
            if (resetData.session_id && onSessionChange) {
              onSessionChange(resetData.session_id)
            }
            // Clear messages from UI and localStorage
            latestMessagesRef.current = []
            setMessages([])
            clearStoredMessages()
            sessionTraceIds.current = []
            dispatchClearHighlights('document-change')
          }
        } catch (resetError) {
          console.error('[Chat] Failed to reset conversation for new document:', resetError)
        }

        // Load the PDF in the viewer when document changes
        try {
          debug.log('[Chat] Fetching PDF metadata for:', detail.document.id)
          await restoreDocumentToPdfViewer(detail.document, () => isActive)

          if (isActive) {
            debug.log('[Chat] Loading PDF in viewer after document change:', detail.document.filename)
          }
        } catch (pdfError) {
          console.warn('[Chat] Unable to load PDF viewer after document change:', pdfError)
        }
      } else {
        if (!isActive) {
          return
        }
        debug.log('[Chat] Clearing active document')
        setActiveDocument(null)
        clearStoredActiveDocument()
      }
    }

    debug.log('[Chat] Setting up chat-document-changed event listener')
    window.addEventListener('chat-document-changed', documentChangeHandler)

    return () => {
      isActive = false
      window.removeEventListener('chat-document-changed', documentChangeHandler)
      clearInterval(interval)
    }
  }, [
    clearStoredActiveDocument,
    clearStoredMessages,
    getStoredActiveDocument,
    onSessionChange,
    restoreDocumentToPdfViewer,
  ])

  const handleCopyMessage = (text: string) => {
    copyText(text).then(() => {
      // Optional: You could show a toast notification here
      debug.log('Message copied to clipboard')
    }).catch(err => {
      console.error('Failed to copy:', err)
    })
  }

  const handleCopySessionId = () => {
    if (!normalizedSessionId) {
      return
    }

    copyText(normalizedSessionId).then(() => {
      setSessionIdCopied(true)

      if (sessionIdCopyTimeoutRef.current) {
        clearTimeout(sessionIdCopyTimeoutRef.current)
      }

      sessionIdCopyTimeoutRef.current = setTimeout(() => {
        setSessionIdCopied(false)
        sessionIdCopyTimeoutRef.current = null
      }, 1500)
    }).catch(err => {
      console.error('Failed to copy session ID:', err)
    })
  }

  const handleFeedbackClick = (messageContent: string, messageTraceIds?: string[]) => {
    if (!feedbackSessionId) {
      emitGlobalToast({
        message: 'Start a chat session before submitting feedback.',
        severity: 'error',
      })
      return
    }

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
    const normalizedSessionId = feedback.session_id.trim()
    if (!normalizedSessionId) {
      throw new Error('Session ID is missing')
    }

    try {
      await submitFeedback({
        ...feedback,
        session_id: normalizedSessionId,
      })
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
      const multiSessionNote = result.prepared_sessions.length > 1
        ? ' Additional prepared sessions are available in Curation Inventory.'
        : ''
      const prepSummary = `${result.summary_text}${warningText}${multiSessionNote}`.trim()
      const targetDocumentId = result.document_id ?? null
      const primaryPreparedSession = result.prepared_sessions[0] ?? null
      const reviewAndCurateTarget = primaryPreparedSession
        ? {
            sessionId: primaryPreparedSession.session_id,
            documentId: targetDocumentId,
            originSessionId: propSessionId,
            adapterKeys: [primaryPreparedSession.adapter_key],
          }
        : targetDocumentId
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

      if (result.prepared_sessions.length > 1) {
        const primaryAdapterLabel = humanizeAdapterKey(primaryPreparedSession.adapter_key)
        const additionalSessionCount = result.prepared_sessions.length - 1
        emitGlobalToast({
          message: (
            `Prepared ${result.prepared_sessions.length} curation sessions. `
            + `Opening ${primaryAdapterLabel} first; `
            + `${additionalSessionCount} additional prepared session`
            + `${additionalSessionCount === 1 ? ' is' : 's are'} available in Curation Inventory.`
          ),
          severity: 'info',
        })
      }

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
        const nextSessionId = normalizeOptionalText(data.session_id)

        if (!nextSessionId) {
          throw new Error('Reset response did not include a replacement session ID.')
        }

        if (storageUserId && propSessionId) {
          clearChatRenderCacheForSession(storageUserId, propSessionId)
        }
        if (storageUserId) {
          clearChatRenderCacheForSession(storageUserId, nextSessionId)
        }

        invalidateTurnRuntimeState()

        if (onSessionChange) {
          debug.log('🔄 [Session Reset] Propagating new session ID to HomePage:', nextSessionId)
          onSessionChange(nextSessionId)
        }

        // Clear messages from UI and localStorage
        latestMessagesRef.current = []
        setMessages([])
        clearStoredMessages()
        sessionTraceIds.current = [] // Clear accumulated trace IDs for new session
        setRefinePrompt(null)
        setRefineText('')
        clearProgressState()
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
        clearStoredActiveDocument()

        // Dispatch event to notify other components (like PDF viewer)
        window.dispatchEvent(
          new CustomEvent('chat-document-changed', {
            detail: {
              active: false,
              document: null,
              ownerToken: HOME_PDF_VIEWER_OWNER,
            },
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

    const messageToSend = inputMessage
    const turnId = buildTurnId()
    activeTurnIdRef.current = turnId
    rescuedTurnIdsRef.current.delete(turnId)
    assistantBuffersRef.current[turnId] = ''

    const userMessage: Message = {
      role: 'user',
      content: messageToSend,
      timestamp: new Date(),
      id: buildUserTurnMessageId(turnId),
      turnId,
    }

    setMessages(prev => [...prev, userMessage])
    setInputMessage('')

    try {
      // Use hook's sendMessage function
      await sendMessage(messageToSend, propSessionId, { turnId })
      setRefinePrompt(null)
      setRefineText('')
    } catch (err) {
      console.error('Error sending message:', err)
      delete assistantBuffersRef.current[turnId]
      if (activeTurnIdRef.current === turnId) {
        activeTurnIdRef.current = null
      }
      setMessages(prev => upsertAssistantTurnMessage(prev, {
        turnId,
        content: 'Sorry, I encountered an error. Please try again.',
        timestamp: new Date(),
        terminalState: 'turn_failed',
        terminalMessage: 'Sorry, I encountered an error. Please try again.',
        rescueState: null,
      }))
    } finally {
      clearProgressState()
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
    const turnId = buildTurnId()
    activeTurnIdRef.current = turnId
    rescuedTurnIdsRef.current.delete(turnId)
    assistantBuffersRef.current[turnId] = ''

    const userMessage: Message = {
      role: 'user',
      content: text,
      timestamp: new Date(),
      id: buildUserTurnMessageId(turnId),
      turnId,
    }

    setMessages(prev => [...prev, userMessage])

    try {
      await sendMessage(text, propSessionId, { turnId })
      setRefinePrompt(null)
    } catch (err) {
      console.error('Error sending quick message:', err)
      delete assistantBuffersRef.current[turnId]
      if (activeTurnIdRef.current === turnId) {
        activeTurnIdRef.current = null
      }
      setMessages(prev => upsertAssistantTurnMessage(prev, {
        turnId,
        content: 'Sorry, I encountered an error. Please try again.',
        timestamp: new Date(),
        terminalState: 'turn_failed',
        terminalMessage: 'Sorry, I encountered an error. Please try again.',
        rescueState: null,
      }))
    } finally {
      clearProgressState()
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

  const hasUnsupportedEvidenceMessages = messages.some(
    (message) =>
      message.role === 'assistant'
      && (message.evidenceRecords?.length ?? 0) > 0
      && message.evidenceCurationSupported === false,
  )
  const unsupportedOnlyPrep =
    prepPreview !== null
    && !prepPreview.ready
    && hasUnsupportedEvidenceMessages
    && prepPreview.preparable_candidate_count === 0
    && prepPreview.extraction_result_count === 0
  const effectivePrepPreview = prepPreview
    ? {
        ...prepPreview,
        summary_text: unsupportedOnlyPrep
          ? UNSUPPORTED_CURATION_REVIEW_MESSAGE
          : prepPreview.summary_text,
        blocking_reasons: unsupportedOnlyPrep
          ? [UNSUPPORTED_CURATION_REVIEW_MESSAGE]
          : prepPreview.blocking_reasons,
      }
    : null
  const prepSupplementalNotice = prepPreview?.ready && hasUnsupportedEvidenceMessages
    ? MIXED_CURATION_PREP_WARNING_MESSAGE
    : null

  const prepButtonLabel = isPreparingCuration
    ? 'Preparing...'
    : isLoadingPrepPreview
      ? 'Loading Scope...'
      : 'Prepare for Curation'

  const prepButtonDisabled = isLoadingPrepPreview || isPreparingCuration || !propSessionId

  const visibleConversationMessageCount = messages.length > 0
    ? messages.filter((message) => message.role === 'user' || message.role === 'assistant').length
    : null

  const handleReviewAndCurateOpened = useCallback((messageId: string, sessionId: string) => {
    setMessages(prev => withUpdatedReviewAndCurateSessionId(prev, messageId, sessionId))
  }, [])

  const handleUnsupportedEvidenceReview = useCallback(() => {
    emitGlobalToast({
      message: UNSUPPORTED_CURATION_REVIEW_MESSAGE,
      severity: 'warning',
    })
  }, [])

  const handleDismissRefinePrompt = useCallback(() => {
    setRefinePrompt(null)
  }, [])

  return {
    activeDocument,
    conversationStatus,
    effectivePrepPreview,
    feedbackDialogOpen,
    feedbackMessageData,
    feedbackSessionId,
    inputMessage,
    isLoading,
    isLoadingPrepPreview,
    isPreparingCuration,
    isResetting,
    isUnloadingPDF,
    limitNotices,
    messages,
    messagesEndRef,
    normalizedSessionId,
    prepButtonDisabled,
    prepButtonLabel,
    prepDialogError,
    prepDialogOpen,
    prepStatus,
    prepSupplementalNotice,
    progressMessage,
    propSessionId,
    refinePrompt,
    refineText,
    sessionIdCopied,
    textareaRef,
    user,
    visibleConversationMessageCount,
    weaviateConnected,
    showCurationDbWarning,
    handleClosePrepDialog,
    handleConfirmPrep,
    handleCopyMessage,
    handleCopySessionId,
    handleDismissRefinePrompt,
    handleFeedbackClick,
    handleFeedbackDialogClose,
    handleFeedbackSubmit,
    handleInputChange,
    handleKeyPress,
    handleOpenCurationWorkspace,
    handleOpenPrepDialog,
    handleRefineSubmit,
    handleResetConversation,
    handleReviewAndCurateOpened,
    handleSendMessage,
    handleSendQuickMessage,
    handleUnloadPDF,
    handleUnsupportedEvidenceReview,
    setRefineText,
  }
}
