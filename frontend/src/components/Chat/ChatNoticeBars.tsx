import { useTheme } from '@mui/material/styles'

import { buildNoticeStyle } from './chatStyles'
import type { PrepStatus } from './types'

interface ChatNoticeBarsProps {
  prepStatus: PrepStatus | null
  limitNotices: string[]
  refinePrompt: string | null
  refineText: string
  weaviateConnected: boolean
  showCurationDbWarning: boolean
  onRefineTextChange: (value: string) => void
  onRefineSubmit: () => void
  onSendQuickMessage: (text: string) => void
  onDismissRefinePrompt: () => void
}

function ChatNoticeBars({
  prepStatus,
  limitNotices,
  refinePrompt,
  refineText,
  weaviateConnected,
  showCurationDbWarning,
  onRefineTextChange,
  onRefineSubmit,
  onSendQuickMessage,
  onDismissRefinePrompt,
}: ChatNoticeBarsProps) {
  const theme = useTheme()
  const prepStatusStyle = prepStatus ? buildNoticeStyle(theme, prepStatus.kind) : undefined

  return (
    <>
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
            borderRadius: '6px',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            ...buildNoticeStyle(theme, 'info'),
          }}
        >
          {limitNotices.map((notice, index) => (
            <span key={index}>{notice}</span>
          ))}
        </div>
      )}

      {refinePrompt && (
        <div
          style={{
            margin: '8px 0',
            padding: '8px 12px',
            borderRadius: '6px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            ...buildNoticeStyle(theme, 'error'),
          }}
        >
          <span>{refinePrompt}</span>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text"
              value={refineText}
              onChange={(event) => onRefineTextChange(event.target.value)}
              placeholder="e.g., Use limit 50 for mouse (MGI)"
              style={{
                padding: '6px 8px',
                borderRadius: '4px',
                border: `1px solid ${theme.palette.divider}`,
                background: theme.palette.background.paper,
                color: theme.palette.text.primary,
                minWidth: '260px',
              }}
            />
            <button
              onClick={onRefineSubmit}
              style={{
                padding: '6px 12px',
                borderRadius: '4px',
                border: `1px solid ${theme.palette.error.main}`,
                background: theme.palette.error.main,
                color: theme.palette.error.contrastText,
                cursor: 'pointer',
              }}
            >
              Send
            </button>
            <button
              onClick={() => onSendQuickMessage('Use limit 50 and add a species/provider filter.')}
              style={{
                padding: '4px 10px',
                borderRadius: '4px',
                border: `1px solid ${theme.palette.error.main}`,
                background: theme.palette.error.main,
                color: theme.palette.error.contrastText,
                cursor: 'pointer',
              }}
            >
              Proceed with limit 50
            </button>
            <button
              onClick={() => onSendQuickMessage('Use limit 100 and add a species/provider filter.')}
              style={{
                padding: '4px 10px',
                borderRadius: '4px',
                border: `1px solid ${theme.palette.error.main}`,
                background: 'transparent',
                color: theme.palette.error.main,
                cursor: 'pointer',
              }}
            >
              Proceed with limit 100
            </button>
            <button
              onClick={onDismissRefinePrompt}
              style={{
                padding: '4px 10px',
                borderRadius: '4px',
                border: `1px solid ${theme.palette.divider}`,
                background: 'transparent',
                color: theme.palette.text.secondary,
                cursor: 'pointer',
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
    </>
  )
}

export default ChatNoticeBars
