import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { debug } from '@/utils/env'
import { Box, Backdrop, CircularProgress, Typography, Stack, Button, Alert } from '@mui/material'
import { alpha, styled } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import Chat from '@/components/Chat'
import RightPanel from '@/components/RightPanel'
import { useAuth } from '@/contexts/AuthContext'
import { useChatStream } from '@/hooks/useChatStream'
import {
  DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
  getChatLocalStorageKeys,
  pruneChatMessageCacheMessages,
} from '@/lib/chatCacheKeys'
import {
  safeGetItem,
  safeGetJson,
  safeRemoveItem,
  safeSetItem,
  safeSetJson,
} from '@/lib/browserStorage'
import { normalizeChatHistoryValue } from '@/lib/chatHistoryNormalization'
import { LatestIntent, type LatestIntentOperation } from '@/lib/latestIntent'
import {
  HOME_PDF_VIEWER_OWNER,
} from '@/components/pdfViewer/pdfEvents'
import {
  rehydrateChatDocumentFromSource,
} from '@/features/documents/chatDocumentRehydration'
import {
  DOCUMENT_LOADING_STORAGE_KEY,
  DOCUMENT_LOAD_COMPLETE_EVENT,
  DOCUMENT_LOAD_ERROR_EVENT,
  DOCUMENT_LOAD_START_EVENT,
  failDocumentLoad,
  startDocumentLoad,
} from '@/features/documents/documentLoadEvents'
import {
  dispatchChatDocumentChanged,
  loadDocumentForChat,
} from '@/features/documents/pdfUploadFlow'
import { readCurationApiError } from '@/features/curation/services/api'
import {
  ASSISTANT_CHAT_HISTORY_KIND,
  buildRestorableChatMessages,
  fetchChatHistoryDetail,
  type ChatHistoryActiveDocument,
  type ChatHistoryDetailResponse,
} from '@/services/chatHistoryApi'

const Root = styled(Box)(({ theme }) => ({
  flex: 1,
  display: 'flex',
  height: '100%',
  minHeight: 0,
  overflow: 'hidden',
  backgroundColor: theme.palette.background.default,
  color: theme.palette.text.primary,
}))

const PanelSection = styled(Box)(() => ({
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  height: '100%',
  paddingTop: 0,
  '& > *': {
    flex: 1,
    minHeight: 0,
    height: '100%',
  },
}))

const ResizeHandle = styled(PanelResizeHandle)(({ theme }) => ({
  width: 4,
  flex: '0 0 4px',
  backgroundColor: theme.palette.divider,
  cursor: 'col-resize',
  transition: 'background-color 0.2s ease',
  borderRadius: theme.shape.borderRadius,
  position: 'relative',
  '&:hover, &[data-resize-handle-active="true"]': {
    backgroundColor: theme.palette.primary.main,
  },
  '&::after': {
    content: '""',
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: 2,
    height: 32,
    borderRadius: 1,
    backgroundColor: alpha(theme.palette.text.primary, theme.palette.mode === 'dark' ? 0.45 : 0.36),
    pointerEvents: 'none',
  },
}))

const RIGHT_PANEL_TAB_KEY = 'home-right-panel-tab'

interface DurableChatSessionResponse {
  session_id: string
  created_at: string
  updated_at: string
  title?: string | null
  active_document_id?: string | null
  active_document?: ChatHistoryActiveDocument | null
}

interface LoadForChatRouteDocument {
  id: string
  filename?: string | null
}

function readLoadForChatRouteDocument(state: unknown): LoadForChatRouteDocument | null {
  const maybeState = state as { loadForChatDocument?: unknown } | null
  const maybeDocument = maybeState?.loadForChatDocument as {
    id?: unknown
    filename?: unknown
  } | null

  if (!maybeDocument || typeof maybeDocument.id !== 'string' || !maybeDocument.id.trim()) {
    return null
  }

  return {
    id: maybeDocument.id,
    filename: typeof maybeDocument.filename === 'string' ? maybeDocument.filename : null,
  }
}

function HomePage() {
  const { user } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedSessionId = normalizeChatHistoryValue(searchParams.get('session'))
  const chatStorageKeys = useMemo(
    () => (user?.uid ? getChatLocalStorageKeys(user.uid) : null),
    [user?.uid],
  )

  // Manage session state at HomePage level for sharing between Chat and RightPanel
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const sessionInitPromiseRef = useRef<Promise<string> | null>(null)
  const latestCreatedSessionRef = useRef<DurableChatSessionResponse | null>(null)
  const handledRouteDocumentLoadRef = useRef<string | null>(null)
  const documentLoadingTimeoutIdRef = useRef<number | null>(null)
  const latestIntentRef = useRef(new LatestIntent())
  const [isBootstrappingSession, setIsBootstrappingSession] = useState(true)
  const [missingSessionId, setMissingSessionId] = useState<string | null>(null)
  const [sessionBootstrapError, setSessionBootstrapError] = useState<string | null>(null)
  const [isStartingNewChat, setIsStartingNewChat] = useState(false)

  // Document loading overlay state
  const [loadingDocument, setLoadingDocument] = useState(false)
  const [loadingError, setLoadingError] = useState<string | null>(null)

  // Right panel tab state (persisted)
  const [rightPanelTab, setRightPanelTab] = useState<number>(() => {
    const stored = safeGetItem(() => window.localStorage, RIGHT_PANEL_TAB_KEY, {
      owner: 'preferences',
      key: RIGHT_PANEL_TAB_KEY,
      quiet: true,
    })
    if (!stored.ok) {
      return 0
    }
    return stored.value ? parseInt(stored.value, 10) : 0
  })

  // Persist tab changes
  const handleRightPanelTabChange = useCallback((tabIndex: number) => {
    setRightPanelTab(tabIndex)
    safeSetItem(() => window.localStorage, RIGHT_PANEL_TAB_KEY, String(tabIndex), {
      owner: 'preferences',
      key: RIGHT_PANEL_TAB_KEY,
    })
  }, [])

  // Single shared SSE stream for both Chat and AuditPanel
  const {
    events,
    eventStreamVersion,
    processedEventCount,
    isLoading,
    sendMessage,
    markEventsProcessed,
    stopStream,
    executeFlow,
  } = useChatStream(sessionId)

  const clearDocumentLoadingTimeout = useCallback(() => {
    if (documentLoadingTimeoutIdRef.current === null) {
      return
    }

    window.clearTimeout(documentLoadingTimeoutIdRef.current)
    documentLoadingTimeoutIdRef.current = null
  }, [])

  const persistSessionId = useCallback((nextSessionId: string | null) => {
    sessionIdRef.current = nextSessionId
    setSessionId(nextSessionId)

    if (!nextSessionId) {
      latestCreatedSessionRef.current = null
    }

    if (chatStorageKeys) {
      if (nextSessionId) {
        safeSetItem(() => window.localStorage, chatStorageKeys.sessionId, nextSessionId, {
          owner: 'chat',
          workflowCritical: true,
        })
      } else {
        safeRemoveItem(() => window.localStorage, chatStorageKeys.sessionId, {
          owner: 'chat',
          workflowCritical: true,
        })
      }
    }

    sessionInitPromiseRef.current = nextSessionId ? Promise.resolve(nextSessionId) : null
  }, [chatStorageKeys])

  const clearPersistedMessages = useCallback(() => {
    if (!chatStorageKeys) {
      return
    }

    safeRemoveItem(() => window.localStorage, chatStorageKeys.messages, {
      owner: 'chat',
      workflowCritical: true,
    })
  }, [chatStorageKeys])

  const persistSessionMessages = useCallback((
    activeSessionId: string,
    detail: ChatHistoryDetailResponse,
  ) => {
    if (!chatStorageKeys) {
      return
    }

    const storedMessages = buildRestorableChatMessages(detail.messages)
    const prunedMessages = pruneChatMessageCacheMessages(storedMessages)
    if (prunedMessages.length === 0) {
      safeRemoveItem(() => window.localStorage, chatStorageKeys.messages, {
        owner: 'chat',
        workflowCritical: true,
      })
      return
    }

    safeSetJson(() => window.localStorage, chatStorageKeys.messages, {
      session_id: activeSessionId,
      messages: prunedMessages,
    }, {
      owner: 'chat',
      workflowCritical: true,
    })
  }, [chatStorageKeys])

  const clearDocumentContext = useCallback(async (operation?: LatestIntentOperation) => {
    if (operation && !operation.ownsLatest()) {
      return
    }
    safeRemoveItem(() => window.sessionStorage, DOCUMENT_LOADING_STORAGE_KEY, {
      owner: 'workflow',
      workflowCritical: true,
    })
    setLoadingDocument(false)

    if (chatStorageKeys) {
      safeRemoveItem(() => window.localStorage, chatStorageKeys.activeDocument, {
        owner: 'chat',
        workflowCritical: true,
      })
      safeRemoveItem(() => window.localStorage, chatStorageKeys.pdfViewerSession, {
        owner: 'pdf-viewer',
        workflowCritical: true,
      })
    }

    try {
      await fetch('/api/chat/document', {
        method: 'DELETE',
        credentials: 'include',
        signal: operation?.signal,
        headers: operation
          ? {
              'X-Chat-Document-Intent-Owner': operation.owner,
              'X-Chat-Document-Intent-Generation': String(operation.generation),
            }
          : undefined,
      })
    } catch (error) {
      console.warn('Failed to clear chat document context', error)
    }

    if (operation && !operation.ownsLatest()) {
      return
    }

    window.dispatchEvent(new CustomEvent('chat-document-changed', {
      detail: {
        active: false,
        document: null,
        ownerToken: HOME_PDF_VIEWER_OWNER,
      },
    }))
  }, [chatStorageKeys])

  const rehydrateDocumentContext = useCallback(async (
    document: ChatHistoryActiveDocument | null | undefined,
    operation: LatestIntentOperation,
  ) => {
    if (!operation.ownsLatest()) {
      return
    }
    if (!document) {
      setLoadingDocument(false)
      setLoadingError(null)
      await clearDocumentContext(operation)
      return
    }

    try {
      setLoadingError(null)
      setLoadingDocument(true)
      startDocumentLoad({
        documentId: document.id,
        filename: document.filename,
        message: `Restoring ${document.filename ?? 'the active document'} for chat...`,
      })

      await rehydrateChatDocumentFromSource({
        loadDocument: async () => document,
        chatStorageKeys,
        ensureLoadedForChat: true,
        ownerToken: HOME_PDF_VIEWER_OWNER,
        operation,
      })
    } catch (error) {
      if (!operation.ownsLatest()) {
        return
      }
      console.error('Failed to restore document context for resumed chat', error)
      failDocumentLoad({
        documentId: document.id,
        filename: document.filename,
        message: `Unable to restore ${document.filename ?? 'the active document'} for this chat session.`,
      })
      setLoadingDocument(false)
      setLoadingError(
        `Unable to restore ${document.filename ?? 'the active document'} for this chat session.`,
      )
      await clearDocumentContext(operation)
    }
  }, [chatStorageKeys, clearDocumentContext])

  // Create new session via backend API
  const createSession = useCallback(async (): Promise<DurableChatSessionResponse> => {
    const response = await fetch('/api/chat/session', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        chat_kind: ASSISTANT_CHAT_HISTORY_KIND,
      }),
    })

    if (!response.ok) {
      throw new Error(await readCurationApiError(response))
    }

    const data = await response.json() as DurableChatSessionResponse
    const nextSessionId = normalizeChatHistoryValue(data.session_id)
    if (!nextSessionId) {
      throw new Error('Chat session response did not include a session ID')
    }

    const normalizedSession = {
      ...data,
      session_id: nextSessionId,
    }
    return normalizedSession
  }, [])

  // Ensure session exists before operations
  const ensureSession = useCallback(async (
    operation?: LatestIntentOperation,
  ): Promise<string> => {
    // Check ref first (in-memory)
    if (sessionIdRef.current) {
      return sessionIdRef.current
    }

    // Check localStorage (persisted from previous navigation)
    const storedSession = chatStorageKeys
      ? safeGetItem(() => window.localStorage, chatStorageKeys.sessionId, {
          owner: 'chat',
          workflowCritical: true,
        })
      : null
    const storedSessionId = storedSession?.ok
      ? normalizeChatHistoryValue(storedSession.value)
      : null
    if (storedSessionId) {
      persistSessionId(storedSessionId)
      return storedSessionId
    }

    // No existing session - create new one
    if (!sessionInitPromiseRef.current) {
      sessionInitPromiseRef.current = createSession()
        .then((session) => {
          latestCreatedSessionRef.current = session
          return session.session_id
        })
        .catch((error) => {
          sessionInitPromiseRef.current = null
          throw error
        })
    }

    const activeSessionId = await sessionInitPromiseRef.current
    if (operation && !operation.ownsLatest()) {
      throw new DOMException('Session initialization superseded', 'AbortError')
    }
    persistSessionId(activeSessionId)
    clearPersistedMessages()
    return activeSessionId
  }, [chatStorageKeys, clearPersistedMessages, createSession, persistSessionId])

  /**
   * Get current document ID from PDF viewer localStorage session
   */
  const getCurrentDocumentId = useCallback((): string | undefined => {
    if (!chatStorageKeys) {
      return undefined
    }

    const session = safeGetJson<{ documentId?: unknown }>(
      () => window.localStorage,
      chatStorageKeys.pdfViewerSession,
      {
        owner: 'pdf-viewer',
        workflowCritical: true,
      },
    )
    return session.ok && typeof session.value?.documentId === 'string'
      ? session.value.documentId
      : undefined
  }, [chatStorageKeys])

  /**
   * Execute a curation flow with current session and document context
   */
  const handleExecuteFlow = useCallback(async (
    flowId: string,
    documentId?: string,
    userQuery?: string
  ) => {
    const currentSessionId = await ensureSession()
    // Use provided documentId or get from PDF viewer
    const docId = documentId || getCurrentDocumentId()
    await executeFlow(flowId, currentSessionId, docId, userQuery)
  }, [ensureSession, executeFlow, getCurrentDocumentId])

  useEffect(() => {
    const operation = latestIntentRef.current.begin()

    const bootstrapSession = async () => {
      if (!user?.uid) {
        return
      }

      setIsBootstrappingSession(true)
      setMissingSessionId(null)
      setSessionBootstrapError(null)

      try {
        if (requestedSessionId) {
          const detail = await fetchChatHistoryDetail({
            sessionId: requestedSessionId,
            chatKind: ASSISTANT_CHAT_HISTORY_KIND,
            messageLimit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
            signal: operation.signal,
          })

          if (!operation.ownsLatest()) {
            return
          }

          const activeSessionId =
            normalizeChatHistoryValue(detail.session.session_id) ?? requestedSessionId
          persistSessionId(activeSessionId)
          persistSessionMessages(activeSessionId, detail)
          await rehydrateDocumentContext(detail.active_document, operation)

          if (operation.ownsLatest()) {
            setIsBootstrappingSession(false)
          }
          return
        }

        const storedSession = chatStorageKeys
          ? safeGetItem(() => window.localStorage, chatStorageKeys.sessionId, {
              owner: 'chat',
              workflowCritical: true,
            })
          : null
        const storedSessionId = storedSession?.ok
          ? normalizeChatHistoryValue(storedSession.value)
          : null
        if (storedSessionId) {
          persistSessionId(storedSessionId)
          if (operation.ownsLatest()) {
            setIsBootstrappingSession(false)
          }
          return
        }

        const activeSessionId = await ensureSession(operation)
        if (!operation.ownsLatest()) {
          return
        }

        const createdSession = latestCreatedSessionRef.current
        await rehydrateDocumentContext(
          createdSession?.session_id === activeSessionId
            ? createdSession.active_document
            : null,
          operation,
        )

        if (operation.ownsLatest()) {
          setIsBootstrappingSession(false)
        }
      } catch (error) {
        if (!operation.ownsLatest()) {
          return
        }

        persistSessionId(null)
        clearPersistedMessages()
        await clearDocumentContext(operation)

        if (!operation.ownsLatest()) {
          return
        }

        const errorMessage = error instanceof Error
          ? error.message
          : 'Unable to initialize the durable chat session.'

        if (
          requestedSessionId
          && errorMessage.toLowerCase().includes('not found')
        ) {
          setMissingSessionId(requestedSessionId)
          setSessionBootstrapError(null)
        } else {
          setMissingSessionId(null)
          setSessionBootstrapError(errorMessage)
        }

        setIsBootstrappingSession(false)
      }
    }

    void bootstrapSession()

    return () => {
      if (operation.ownsLatest()) {
        latestIntentRef.current.invalidate()
      }
    }
  }, [
    chatStorageKeys,
    clearDocumentContext,
    clearPersistedMessages,
    ensureSession,
    persistSessionId,
    persistSessionMessages,
    requestedSessionId,
    rehydrateDocumentContext,
    user?.uid,
  ])

  // Keep ref in sync with state
  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    if (isBootstrappingSession || !user?.uid) {
      return
    }

    const routeDocument = readLoadForChatRouteDocument(location.state)
    if (!routeDocument) {
      return
    }

    const routeLoadKey = routeDocument.id
    if (handledRouteDocumentLoadRef.current === routeLoadKey) {
      return
    }
    handledRouteDocumentLoadRef.current = routeLoadKey
    const operation = latestIntentRef.current.begin()

    navigate(
      {
        pathname: location.pathname,
        search: location.search,
        hash: location.hash,
      },
      { replace: true, state: null },
    )

    const loadRouteDocument = async () => {
      startDocumentLoad({
        documentId: routeDocument.id,
        filename: routeDocument.filename,
        message: `Loading ${routeDocument.filename ?? 'document'} for chat...`,
      })
      setLoadingDocument(true)
      setLoadingError(null)

      try {
        const payload = await loadDocumentForChat(routeDocument.id, {
          signal: operation.signal,
          intentOwner: operation.owner,
          intentGeneration: operation.generation,
        })
        if (handledRouteDocumentLoadRef.current !== routeLoadKey || !operation.ownsLatest()) {
          return
        }
        window.setTimeout(() => {
          if (handledRouteDocumentLoadRef.current === routeLoadKey && operation.ownsLatest()) {
            dispatchChatDocumentChanged(payload)
          }
        }, 0)
      } catch (error) {
        if (handledRouteDocumentLoadRef.current !== routeLoadKey || !operation.ownsLatest()) {
          return
        }
        const message = error instanceof Error
          ? error.message
          : `Failed to load ${routeDocument.filename ?? 'the document'} for chat.`
        failDocumentLoad({
          documentId: routeDocument.id,
          filename: routeDocument.filename,
          message,
        })
        setLoadingDocument(true)
        setLoadingError(message)
      }
    }

    void loadRouteDocument()
  }, [
    isBootstrappingSession,
    location.hash,
    location.pathname,
    location.search,
    location.state,
    navigate,
    user?.uid,
  ])

  // Handle document loading overlay with timeout safety net
  useEffect(() => {
    // Check if we're in the middle of loading a document (e.g., after navigation)
    const loadingMarker = safeGetItem(() => window.sessionStorage, DOCUMENT_LOADING_STORAGE_KEY, {
      owner: 'workflow',
      workflowCritical: true,
    })
    if (loadingMarker.ok && loadingMarker.value === 'true') {
      setLoadingDocument(true)
    }

    const handleLoadStart = () => {
      debug.log('[HomePage] Document load started')
      setLoadingDocument(true)
      setLoadingError(null)
    }

    const handleLoadComplete = () => {
      debug.log('[HomePage] Document load complete')
      clearDocumentLoadingTimeout()
      setLoadingDocument(false)
      setLoadingError(null)
    }

    const handleLoadError = (event: Event) => {
      const detail = (event as CustomEvent<{ message?: string }>).detail
      const message = detail?.message ?? 'Document loaded for chat, but the PDF viewer could not be restored.'
      debug.log('[HomePage] Document load error', message)
      clearDocumentLoadingTimeout()
      setLoadingDocument(true)
      setLoadingError(message)
    }

    window.addEventListener(DOCUMENT_LOAD_START_EVENT, handleLoadStart)
    window.addEventListener(DOCUMENT_LOAD_COMPLETE_EVENT, handleLoadComplete)
    window.addEventListener(DOCUMENT_LOAD_ERROR_EVENT, handleLoadError)

    return () => {
      window.removeEventListener(DOCUMENT_LOAD_START_EVENT, handleLoadStart)
      window.removeEventListener(DOCUMENT_LOAD_COMPLETE_EVENT, handleLoadComplete)
      window.removeEventListener(DOCUMENT_LOAD_ERROR_EVENT, handleLoadError)
    }
  }, [clearDocumentLoadingTimeout])

  // Timeout safety net: if loading takes too long, show an error
  useEffect(() => {
    if (!loadingDocument || loadingError) {
      clearDocumentLoadingTimeout()
      return
    }

    const timeoutId = window.setTimeout(() => {
      const message = 'Document loading timed out before the chat handoff completed. The PDF may still be processing, unavailable, or too large.'
      debug.log('[HomePage] Document loading timeout - showing error')
      failDocumentLoad({ message })
    }, 30000) // 30 second timeout
    documentLoadingTimeoutIdRef.current = timeoutId

    return () => {
      if (documentLoadingTimeoutIdRef.current === timeoutId) {
        documentLoadingTimeoutIdRef.current = null
      }
      window.clearTimeout(timeoutId)
    }
  }, [clearDocumentLoadingTimeout, loadingDocument, loadingError])

  // Dismiss loading overlay and clear error state
  const handleDismissLoading = useCallback(() => {
    clearDocumentLoadingTimeout()
    setLoadingDocument(false)
    setLoadingError(null)
    safeRemoveItem(() => window.sessionStorage, DOCUMENT_LOADING_STORAGE_KEY, {
      owner: 'workflow',
      workflowCritical: true,
    })
  }, [clearDocumentLoadingTimeout])

  const handleStartNewChat = useCallback(async () => {
    const operation = latestIntentRef.current.begin()
    setIsBootstrappingSession(true)
    setIsStartingNewChat(true)
    setMissingSessionId(null)
    setSessionBootstrapError(null)
    setLoadingError(null)

    try {
      const createdSession = await createSession()
      if (!operation.ownsLatest()) {
        return
      }
      persistSessionId(createdSession.session_id)
      clearPersistedMessages()
      latestCreatedSessionRef.current = createdSession
      await rehydrateDocumentContext(createdSession.active_document, operation)

      if (!operation.ownsLatest()) {
        return
      }

      const nextSearchParams = new URLSearchParams(searchParams)
      nextSearchParams.delete('session')
      setSearchParams(nextSearchParams, { replace: true })
      setIsBootstrappingSession(false)
    } catch (error) {
      if (!operation.ownsLatest()) {
        return
      }
      setSessionBootstrapError(
        error instanceof Error
          ? error.message
          : 'Unable to start a new durable chat session.',
      )
      setIsBootstrappingSession(false)
    } finally {
      if (operation.ownsLatest()) {
        setIsStartingNewChat(false)
      }
    }
  }, [clearPersistedMessages, createSession, persistSessionId, rehydrateDocumentContext, searchParams, setSearchParams])

  // Handle session changes from child components (e.g., Chat reset)
  const handleSessionChange = useCallback((newSessionId: string) => {
    debug.log('🔄 [HomePage] Session ID changed:', newSessionId)
    persistSessionId(newSessionId)
    if (chatStorageKeys) {
      safeRemoveItem(() => window.localStorage, chatStorageKeys.messages, {
        owner: 'chat',
        workflowCritical: true,
      })
    }
  }, [chatStorageKeys, persistSessionId])

  if (isBootstrappingSession || !user?.uid) {
    return (
      <Root>
        <Stack spacing={2} alignItems="center" justifyContent="center" sx={{ flex: 1 }}>
          <CircularProgress />
          <Typography variant="h6">
            {requestedSessionId ? 'Restoring chat session...' : 'Preparing chat session...'}
          </Typography>
        </Stack>
      </Root>
    )
  }

  if (missingSessionId || sessionBootstrapError) {
    return (
      <Root>
        <Stack
          spacing={2}
          alignItems="center"
          justifyContent="center"
          sx={{ flex: 1, px: 3, py: 4, maxWidth: 720, mx: 'auto' }}
        >
          <Alert severity={missingSessionId ? 'warning' : 'error'} sx={{ width: '100%' }}>
            {missingSessionId
              ? 'This chat session is unavailable. It may have been deleted.'
              : sessionBootstrapError}
          </Alert>
          <Button
            variant="contained"
            onClick={handleStartNewChat}
            disabled={isStartingNewChat}
          >
            {isStartingNewChat ? 'Starting...' : 'Start new chat'}
          </Button>
        </Stack>
      </Root>
    )
  }

  return (
    <Root>
      <PanelGroup
        direction="horizontal"
        autoSaveId="home-content-panels"
        style={{ width: '100%', height: '100%', display: 'flex', overflow: 'hidden' }}
      >
        <Panel defaultSize={50} minSize={24} maxSize={76}>
          <Box
            sx={{
              height: '100%',
              flex: 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <Chat
              sessionId={sessionId}
              onSessionChange={handleSessionChange}
              events={events}
              eventStreamVersion={eventStreamVersion}
              processedEventCount={processedEventCount}
              isLoading={isLoading}
              sendMessage={sendMessage}
              markEventsProcessed={markEventsProcessed}
            />
          </Box>
        </Panel>

        <ResizeHandle />

        <Panel defaultSize={50} minSize={20} maxSize={58}>
          <PanelSection sx={{ pl: 1 }}>
            <RightPanel
              sessionId={sessionId}
              sseEvents={events}
              onStop={() => sessionId && stopStream(sessionId)}
              isStreaming={isLoading}
              onExecuteFlow={handleExecuteFlow}
              currentDocumentId={getCurrentDocumentId()}
              activeTabIndex={rightPanelTab}
              onTabChange={handleRightPanelTabChange}
            />
          </PanelSection>
        </Panel>
      </PanelGroup>

      {/* Loading overlay when loading a document */}
      <Backdrop
        sx={{
          color: (theme) => theme.palette.common.white,
          zIndex: (theme) => theme.zIndex.drawer + 1,
          backdropFilter: 'blur(4px)',
        }}
        open={loadingDocument}
      >
        <Stack spacing={2} alignItems="center" sx={{ maxWidth: 400 }}>
          {loadingError ? (
            <>
              <Alert
                severity="error"
                sx={{
                  width: '100%',
                  '& .MuiAlert-message': { color: 'inherit' }
                }}
              >
                {loadingError}
              </Alert>
              <Button
                variant="contained"
                color="primary"
                onClick={handleDismissLoading}
                sx={{ mt: 1 }}
              >
                Dismiss
              </Button>
            </>
          ) : (
            <>
              <CircularProgress color="inherit" size={60} />
              <Typography variant="h6" color="inherit">
                Loading document...
              </Typography>
            </>
          )}
        </Stack>
      </Backdrop>
    </Root>
  )
}

export default HomePage
