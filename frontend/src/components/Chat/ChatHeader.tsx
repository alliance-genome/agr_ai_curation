import { useId } from 'react'

import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { useTheme } from '@mui/material/styles'

import { buildSolidButtonStyle } from './chatStyles'
import type { ActiveDocument, ConversationStatus } from './types'

interface ChatHeaderProps {
  activeDocument: ActiveDocument | null
  conversationStatus: ConversationStatus | null
  normalizedSessionId: string | null
  sessionIdCopied: boolean
  isResetting: boolean
  isUnloadingPDF: boolean
  prepButtonDisabled: boolean
  prepButtonLabel: string
  onCopySessionId: () => void
  onResetConversation: () => void
  onUnloadPDF: () => void
  onOpenPrepDialog: () => void
}

function formatSessionPreview(sessionId: string) {
  if (sessionId.length <= 20) {
    return sessionId
  }

  return `${sessionId.slice(0, 8)}...${sessionId.slice(-6)}`
}

function ChatHeader({
  activeDocument,
  conversationStatus,
  normalizedSessionId,
  sessionIdCopied,
  isResetting,
  isUnloadingPDF,
  prepButtonDisabled,
  prepButtonLabel,
  onCopySessionId,
  onResetConversation,
  onUnloadPDF,
  onOpenPrepDialog,
}: ChatHeaderProps) {
  const theme = useTheme()
  const sessionDescriptionId = useId()
  const resetButtonStyle = buildSolidButtonStyle(theme, theme.palette.error.main, isResetting)
  const unloadButtonStyle = buildSolidButtonStyle(
    theme,
    theme.palette.mode === 'dark' ? theme.palette.grey[700] : theme.palette.grey[600],
    isUnloadingPDF,
  )
  const prepButtonStyle = buildSolidButtonStyle(
    theme,
    theme.palette.success.main,
    prepButtonDisabled,
  )
  const activeDocumentLabel = activeDocument
    ? `Active PDF: ${activeDocument.filename ?? activeDocument.id}`
    : 'No PDF loaded'
  const memoryFileCount =
    conversationStatus?.memory_stats?.memory_sizes?.short_term?.file_count ?? 0
  const memoryLabel = `${memoryFileCount} memory ${memoryFileCount === 1 ? 'item' : 'items'}`

  return (
    <div className="chat-header">
      <div className="chat-header__main">
        <div className="chat-header__summary">
          <h2>AI Assistant Chat</h2>
          <div className="chat-status" aria-label="Chat context">
            <span
              className="chat-header__document"
              title={activeDocumentLabel}
            >
              {activeDocumentLabel}
            </span>

            {(conversationStatus || normalizedSessionId) && (
              <span className="chat-header__meta" aria-label="Conversation metadata">
                {conversationStatus && (
                  <span title={`Short-term memory: ${memoryLabel}`}>
                    {memoryLabel}
                  </span>
                )}

                {normalizedSessionId && (
                  <span className="chat-header__session">
                    <span
                      className="chat-header__session-preview"
                      title={`Full session ID: ${normalizedSessionId}`}
                    >
                      Session: {formatSessionPreview(normalizedSessionId)}
                    </span>
                    <span
                      id={sessionDescriptionId}
                      className="chat-header__sr-only"
                    >
                      Full session ID: {normalizedSessionId}
                    </span>
                    <button
                      type="button"
                      onClick={onCopySessionId}
                      aria-label="Copy session ID"
                      aria-describedby={sessionDescriptionId}
                      title={`Copy session ID: ${normalizedSessionId}`}
                      className="chat-header__icon-button"
                      style={{
                        color: theme.palette.text.secondary,
                      }}
                    >
                      <ContentCopyIcon fontSize="inherit" />
                    </button>
                    {sessionIdCopied && (
                      <span role="status" aria-live="polite">
                        Copied!
                      </span>
                    )}
                  </span>
                )}
              </span>
            )}
          </div>
        </div>

        <div className="chat-header__actions">
          <button
            onClick={onResetConversation}
            disabled={isResetting}
            style={{
              padding: '4px 12px',
              backgroundColor: resetButtonStyle.backgroundColor,
              color: resetButtonStyle.color,
              border: 'none',
              borderRadius: '4px',
              cursor: resetButtonStyle.cursor,
              fontSize: '0.9em',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              whiteSpace: 'nowrap',
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
              onClick={onUnloadPDF}
              disabled={isUnloadingPDF}
              style={{
                padding: '4px 12px',
                backgroundColor: unloadButtonStyle.backgroundColor,
                color: unloadButtonStyle.color,
                border: 'none',
                borderRadius: '4px',
                cursor: unloadButtonStyle.cursor,
                fontSize: '0.9em',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                whiteSpace: 'nowrap',
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
            onClick={onOpenPrepDialog}
            disabled={prepButtonDisabled}
            style={{
              padding: '4px 12px',
              backgroundColor: prepButtonStyle.backgroundColor,
              color: prepButtonStyle.color,
              border: 'none',
              borderRadius: '4px',
              cursor: prepButtonStyle.cursor,
              fontSize: '0.9em',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              whiteSpace: 'nowrap',
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
  )
}

export default ChatHeader
