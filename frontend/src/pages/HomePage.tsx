import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { debug } from '@/utils/env'
import { Box, Backdrop, CircularProgress, Typography, Stack, Button, Alert } from '@mui/material'
import { alpha, styled } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'

import Chat from '@/components/Chat'
import RightPanel from '@/components/RightPanel'
import { useAuth } from '@/contexts/AuthContext'
import { useChatStream } from '@/hooks/useChatStream'
import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'

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

function HomePage() {
  const { user } = useAuth()
  const chatStorageKeys = useMemo(
    () => (user?.uid ? getChatLocalStorageKeys(user.uid) : null),
    [user?.uid],
  )

  // Manage session state at HomePage level for sharing between Chat and RightPanel
  // Initialize from localStorage if available
  const [sessionId, setSessionId] = useState<string | null>(() => {
    return chatStorageKeys ? localStorage.getItem(chatStorageKeys.sessionId) : null
  })
  const sessionIdRef = useRef<string | null>(
    chatStorageKeys ? localStorage.getItem(chatStorageKeys.sessionId) : null,
  )
  const sessionInitPromiseRef = useRef<Promise<string> | null>(null)

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

  // Generate local fallback session ID
  const generateLocalSessionId = useCallback(() => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }

    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = Math.random() * 16 | 0
      const v = c === 'x' ? r : (r & 0x3) | 0x8
      return v.toString(16)
    })
  }, [])

  // Create new session via backend API
  const createSession = useCallback(async (): Promise<string> => {
    try {
      const response = await fetch('/api/chat/session', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        }
      })

      if (response.ok) {
        const data = await response.json()
        sessionIdRef.current = data.session_id
        setSessionId(data.session_id)
        // Persist to localStorage for navigation persistence
        if (chatStorageKeys) {
          localStorage.setItem(chatStorageKeys.sessionId, data.session_id)
        }
        return data.session_id
      }

      console.error('Failed to create session:', response.status, response.statusText)
    } catch (error) {
      console.error('Error creating chat session', error)
    }

    const fallbackId = generateLocalSessionId()
    sessionIdRef.current = fallbackId
    setSessionId(fallbackId)
    // Persist fallback ID too
    if (chatStorageKeys) {
      localStorage.setItem(chatStorageKeys.sessionId, fallbackId)
    }
    return fallbackId
  }, [chatStorageKeys, generateLocalSessionId])

  // Ensure session exists before operations
  const ensureSession = useCallback(async (): Promise<string> => {
    // Check ref first (in-memory)
    if (sessionIdRef.current) {
      return sessionIdRef.current
    }

    // Check localStorage (persisted from previous navigation)
    const storedSessionId = chatStorageKeys ? localStorage.getItem(chatStorageKeys.sessionId) : null
    if (storedSessionId) {
      sessionIdRef.current = storedSessionId
      setSessionId(storedSessionId)
      return storedSessionId
    }

    // No existing session - create new one
    if (!sessionInitPromiseRef.current) {
      sessionInitPromiseRef.current = createSession()
    }

    const activeSessionId = await sessionInitPromiseRef.current
    sessionIdRef.current = activeSessionId
    setSessionId(activeSessionId)
    return activeSessionId
  }, [chatStorageKeys, createSession])

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
    const storedSessionId = chatStorageKeys ? localStorage.getItem(chatStorageKeys.sessionId) : null
    sessionIdRef.current = storedSessionId
    setSessionId(storedSessionId)
    sessionInitPromiseRef.current = storedSessionId ? Promise.resolve(storedSessionId) : null
  }, [chatStorageKeys])

  // Initialize session on mount
  useEffect(() => {
    void ensureSession()
  }, [ensureSession])

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

  // Handle session changes from child components (e.g., Chat reset)
  const handleSessionChange = useCallback((newSessionId: string) => {
    debug.log('🔄 [HomePage] Session ID changed:', newSessionId)
    sessionIdRef.current = newSessionId
    setSessionId(newSessionId)
    // Persist to localStorage
    if (chatStorageKeys) {
      localStorage.setItem(chatStorageKeys.sessionId, newSessionId)
      localStorage.removeItem(chatStorageKeys.messages)
    }
    // Clear the init promise so future ensureSession calls use the new ID
    sessionInitPromiseRef.current = Promise.resolve(newSessionId)
  }, [chatStorageKeys])

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
