import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { debug } from '@/utils/env'
import { Box, Backdrop, CircularProgress, Typography, Stack, Button, Alert } from '@mui/material'
import { alpha, styled } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { useSearchParams } from 'react-router-dom'

import Chat from '@/components/Chat'
import RightPanel from '@/components/RightPanel'
import { useAuth } from '@/contexts/AuthContext'
import { useChatStream } from '@/hooks/useChatStream'
import {
  DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
  getChatLocalStorageKeys,
} from '@/lib/chatCacheKeys'
import { normalizeChatHistoryValue } from '@/lib/chatHistoryNormalization'
import {
  HOME_PDF_VIEWER_OWNER,
} from '@/components/pdfViewer/pdfEvents'
import {
  rehydrateChatDocumentFromSource,
} from '@/features/documents/chatDocumentRehydration'
import { readCurationApiError } from '@/features/curation/services/api'
import {
  buildRestorableChatMessages,
  fetchChatHistoryDetail,
  type ChatHistoryActiveDocument,
  type ChatHistoryDetailResponse,
} from '@/services/chatHistoryApi'

const Root = styled(Box)(() => ({
  flex: 1,
  display: 'flex',
  height: '100%',
  minHeight: 0,
  overflow: 'hidden',
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
    backgroundColor: alpha(theme.palette.common.white, 0.45),
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

function HomePage() {
  const { user } = useAuth()
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
  const [isBootstrappingSession, setIsBootstrappingSession] = useState(true)
  const [missingSessionId, setMissingSessionId] = useState<string | null>(null)
  const [sessionBootstrapError, setSessionBootstrapError] = useState<string | null>(null)
  const [isStartingNewChat, setIsStartingNewChat] = useState(false)

  // Document loading overlay state
  const [loadingDocument, setLoadingDocument] = useState(false)
  const [loadingError, setLoadingError] = useState<string | null>(null)

  // Right panel tab state (persisted)
  const [rightPanelTab, setRightPanelTab] = useState<number>(() => {
    const stored = localStorage.getItem(RIGHT_PANEL_TAB_KEY)
    return stored ? parseInt(stored, 10) : 0
  })

  // Persist tab changes
  const handleRightPanelTabChange = useCallback((tabIndex: number) => {
    setRightPanelTab(tabIndex)
    localStorage.setItem(RIGHT_PANEL_TAB_KEY, String(tabIndex))
  }, [])

  // Single shared SSE stream for both Chat and AuditPanel
  const { events, isLoading, sendMessage, stopStream, executeFlow } = useChatStream()

  const persistSessionId = useCallback((nextSessionId: string | null) => {
    sessionIdRef.current = nextSessionId
    setSessionId(nextSessionId)

    if (!nextSessionId) {
      latestCreatedSessionRef.current = null
    }

    if (chatStorageKeys) {
      if (nextSessionId) {
        localStorage.setItem(chatStorageKeys.sessionId, nextSessionId)
      } else {
        localStorage.removeItem(chatStorageKeys.sessionId)
      }
    }

    sessionInitPromiseRef.current = nextSessionId ? Promise.resolve(nextSessionId) : null
  }, [chatStorageKeys])

  const clearPersistedMessages = useCallback(() => {
    if (!chatStorageKeys) {
      return
    }

    localStorage.removeItem(chatStorageKeys.messages)
  }, [chatStorageKeys])

  const persistSessionMessages = useCallback((
    activeSessionId: string,
    detail: ChatHistoryDetailResponse,
  ) => {
    if (!chatStorageKeys) {
      return
    }

    const storedMessages = buildRestorableChatMessages(detail.messages)
    if (storedMessages.length === 0) {
      localStorage.removeItem(chatStorageKeys.messages)
      return
    }

    localStorage.setItem(chatStorageKeys.messages, JSON.stringify({
      session_id: activeSessionId,
      messages: storedMessages,
    }))
  }, [chatStorageKeys])

  const clearDocumentContext = useCallback(async () => {
    sessionStorage.removeItem('document-loading')
    setLoadingDocument(false)

    if (chatStorageKeys) {
      localStorage.removeItem(chatStorageKeys.activeDocument)
      localStorage.removeItem(chatStorageKeys.pdfViewerSession)
    }

    try {
      await fetch('/api/chat/document', {
        method: 'DELETE',
        credentials: 'include',
      })
    } catch (error) {
      console.warn('Failed to clear chat document context', error)
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
  ) => {
    if (!document) {
      setLoadingDocument(false)
      setLoadingError(null)
      await clearDocumentContext()
      return
    }

    try {
      setLoadingError(null)
      setLoadingDocument(true)
      sessionStorage.setItem('document-loading', 'true')
      window.dispatchEvent(new CustomEvent('document-load-start'))

      await rehydrateChatDocumentFromSource({
        loadDocument: async () => document,
        chatStorageKeys,
        ensureLoadedForChat: true,
        ownerToken: HOME_PDF_VIEWER_OWNER,
      })
    } catch (error) {
      console.error('Failed to restore document context for resumed chat', error)
      sessionStorage.removeItem('document-loading')
      setLoadingDocument(false)
      setLoadingError(
        `Unable to restore ${document.filename ?? 'the active document'} for this chat session.`,
      )
      await clearDocumentContext()
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
    })

    if (!response.ok) {
      throw new Error(await readCurationApiError(response))
    }

    const data = await response.json() as DurableChatSessionResponse
    const nextSessionId = normalizeChatHistoryValue(data.session_id)
    if (!nextSessionId) {
      throw new Error('Chat session response did not include a session ID')
    }

    persistSessionId(nextSessionId)
    clearPersistedMessages()

    const normalizedSession = {
      ...data,
      session_id: nextSessionId,
    }
    latestCreatedSessionRef.current = normalizedSession

    return normalizedSession
  }, [clearPersistedMessages, persistSessionId])

  // Ensure session exists before operations
  const ensureSession = useCallback(async (): Promise<string> => {
    // Check ref first (in-memory)
    if (sessionIdRef.current) {
      return sessionIdRef.current
    }

    // Check localStorage (persisted from previous navigation)
    const storedSessionId = chatStorageKeys
      ? normalizeChatHistoryValue(localStorage.getItem(chatStorageKeys.sessionId))
      : null
    if (storedSessionId) {
      persistSessionId(storedSessionId)
      return storedSessionId
    }

    // No existing session - create new one
    if (!sessionInitPromiseRef.current) {
      sessionInitPromiseRef.current = createSession()
        .then((session) => session.session_id)
        .catch((error) => {
          sessionInitPromiseRef.current = null
          throw error
        })
    }

    const activeSessionId = await sessionInitPromiseRef.current
    persistSessionId(activeSessionId)
    return activeSessionId
  }, [chatStorageKeys, createSession, persistSessionId])

  /**
   * Get current document ID from PDF viewer localStorage session
   */
  const getCurrentDocumentId = useCallback((): string | undefined => {
    try {
      if (!chatStorageKeys) {
        return undefined
      }

      const raw = localStorage.getItem(chatStorageKeys.pdfViewerSession)
      if (!raw) return undefined
      const session = JSON.parse(raw)
      return session?.documentId || undefined
    } catch {
      return undefined
    }
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
    let cancelled = false

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
            messageLimit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
          })

          if (cancelled) {
            return
          }

          const activeSessionId =
            normalizeChatHistoryValue(detail.session.session_id) ?? requestedSessionId
          persistSessionId(activeSessionId)
          persistSessionMessages(activeSessionId, detail)
          await rehydrateDocumentContext(detail.active_document)

          if (!cancelled) {
            setIsBootstrappingSession(false)
          }
          return
        }

        const storedSessionId = chatStorageKeys
          ? normalizeChatHistoryValue(localStorage.getItem(chatStorageKeys.sessionId))
          : null
        if (storedSessionId) {
          persistSessionId(storedSessionId)
          if (!cancelled) {
            setIsBootstrappingSession(false)
          }
          return
        }

        const activeSessionId = await ensureSession()
        if (cancelled) {
          return
        }

        const createdSession = latestCreatedSessionRef.current
        await rehydrateDocumentContext(
          createdSession?.session_id === activeSessionId
            ? createdSession.active_document
            : null,
        )

        if (!cancelled) {
          setIsBootstrappingSession(false)
        }
      } catch (error) {
        if (cancelled) {
          return
        }

        persistSessionId(null)
        clearPersistedMessages()
        await clearDocumentContext()

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
      cancelled = true
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

  // Handle document loading overlay with timeout safety net
  useEffect(() => {
    // Check if we're in the middle of loading a document (e.g., after navigation)
    if (sessionStorage.getItem('document-loading') === 'true') {
      setLoadingDocument(true)
    }

    const handleLoadStart = () => {
      debug.log('[HomePage] Document load started')
      setLoadingDocument(true)
      setLoadingError(null)
    }

    const handleLoadComplete = () => {
      debug.log('[HomePage] Document load complete')
      setLoadingDocument(false)
      setLoadingError(null)
    }

    window.addEventListener('document-load-start', handleLoadStart)
    window.addEventListener('document-load-complete', handleLoadComplete)

    return () => {
      window.removeEventListener('document-load-start', handleLoadStart)
      window.removeEventListener('document-load-complete', handleLoadComplete)
    }
  }, [])

  // Timeout safety net: if loading takes too long, show an error
  useEffect(() => {
    if (!loadingDocument) return

    const timeoutId = window.setTimeout(() => {
      debug.log('[HomePage] Document loading timeout - showing error')
      setLoadingError('Document loading timed out. The PDF may be unavailable or too large.')
    }, 30000) // 30 second timeout

    return () => {
      window.clearTimeout(timeoutId)
    }
  }, [loadingDocument])

  // Dismiss loading overlay and clear error state
  const handleDismissLoading = useCallback(() => {
    setLoadingDocument(false)
    setLoadingError(null)
    sessionStorage.removeItem('document-loading')
  }, [])

  const handleStartNewChat = useCallback(async () => {
    setIsBootstrappingSession(true)
    setIsStartingNewChat(true)
    setMissingSessionId(null)
    setSessionBootstrapError(null)
    setLoadingError(null)

    try {
      const createdSession = await createSession()
      await rehydrateDocumentContext(createdSession.active_document)

      const nextSearchParams = new URLSearchParams(searchParams)
      nextSearchParams.delete('session')
      setSearchParams(nextSearchParams, { replace: true })
      setIsBootstrappingSession(false)
    } catch (error) {
      setSessionBootstrapError(
        error instanceof Error
          ? error.message
          : 'Unable to start a new durable chat session.',
      )
      setIsBootstrappingSession(false)
    } finally {
      setIsStartingNewChat(false)
    }
  }, [createSession, rehydrateDocumentContext, searchParams, setSearchParams])

  // Handle session changes from child components (e.g., Chat reset)
  const handleSessionChange = useCallback((newSessionId: string) => {
    debug.log('🔄 [HomePage] Session ID changed:', newSessionId)
    persistSessionId(newSessionId)
    if (chatStorageKeys) {
      localStorage.removeItem(chatStorageKeys.messages)
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
              isLoading={isLoading}
              sendMessage={sendMessage}
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
          color: '#fff',
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
