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

  return (
    <div className="chat-header">
      <h2>AI Assistant Chat</h2>
      <div className="chat-status">
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
          {activeDocument ? (
            <span>
              Active PDF: {activeDocument.filename || activeDocument.id}
            </span>
          ) : (
            <span>No PDF loaded</span>
          )}

          {conversationStatus && (
            <span style={{ fontSize: '0.9em', color: theme.palette.text.secondary }}>
              Memory: {
                conversationStatus.memory_stats?.memory_sizes?.short_term?.file_count || 0
              } items
            </span>
          )}

          {normalizedSessionId && (
            <span
              style={{
                fontSize: '0.9em',
                color: theme.palette.text.secondary,
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                flexWrap: 'wrap',
              }}
            >
              <span>Session:</span>
              <span
                style={{
                  fontFamily: 'SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace',
                  overflowWrap: 'anywhere',
                }}
              >
                {normalizedSessionId}
              </span>
              <button
                type="button"
                onClick={onCopySessionId}
                aria-label="Copy session ID"
                title="Copy session ID"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: 0,
                  border: 'none',
                  background: 'transparent',
                  color: theme.palette.text.secondary,
                  cursor: 'pointer',
                  fontSize: '0.9em',
                  lineHeight: 1,
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
