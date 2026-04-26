import type { RefObject } from 'react'

import { useTheme } from '@mui/material/styles'

import type { CurationWorkspaceLaunchTarget } from '@/features/curation/navigation/openCurationWorkspace'

import EvidenceCard from './EvidenceCard'
import FileDownloadCard from './FileDownloadCard'
import FlowStepEvidenceCard from './FlowStepEvidenceCard'
import MessageActions from './MessageActions'
import { buildAssistantNoticeStyle } from './chatStyles'
import { getAssistantStatusNotice } from './chatMessageUtils'
import type { ChatCssVariables, Message } from './types'

interface ChatMessageListProps {
  messages: Message[]
  isLoading: boolean
  progressMessage: string
  messagesEndRef: RefObject<HTMLDivElement>
  chatCssVariables: ChatCssVariables
  sessionId: string | null
  onCopyMessage: (text: string) => void
  onFeedbackClick: (messageContent: string, messageTraceIds?: string[]) => void
  onOpenCurationWorkspace: (
    target: CurationWorkspaceLaunchTarget,
    options?: { messageId?: string },
  ) => Promise<string | null>
  onReviewAndCurateOpened: (messageId: string, sessionId: string) => void
  onUnsupportedEvidenceReview: () => void
}

function ChatMessageList({
  messages,
  isLoading,
  progressMessage,
  messagesEndRef,
  chatCssVariables,
  sessionId,
  onCopyMessage,
  onFeedbackClick,
  onOpenCurationWorkspace,
  onReviewAndCurateOpened,
  onUnsupportedEvidenceReview,
}: ChatMessageListProps) {
  const theme = useTheme()

  return (
    <div className="messages-container" data-testid="messages-container" style={{
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
      borderTop: `1px solid ${chatCssVariables['--chat-subtle-divider']}`,
      borderBottom: `1px solid ${chatCssVariables['--chat-subtle-divider']}`,
    }}>
      {messages.length === 0 ? (
        <div className="empty-state">
          Ask a question to get started...
        </div>
      ) : (
        messages.map((message, index) => {
          const hasEvidenceCard = (message.evidenceRecords?.length ?? 0) > 0
          const assistantStatusNotice = getAssistantStatusNotice(message)
          const handleEvidenceReviewAndCurateClick = message.reviewAndCurateTarget
            ? () => {
                const messageId = message.id
                void onOpenCurationWorkspace(
                  message.reviewAndCurateTarget!,
                  messageId ? { messageId } : undefined,
                )
              }
            : message.evidenceCurationSupported === false
              ? onUnsupportedEvidenceReview
              : null

          if (message.role === 'flow' && message.flowStepEvidence) {
            return (
              <FlowStepEvidenceCard
                details={message.flowStepEvidence}
                key={message.id || index}
              />
            )
          }

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
                  {assistantStatusNotice ? (
                    <div
                      role={assistantStatusNotice.tone === 'info' ? 'status' : 'alert'}
                      style={{
                        marginTop: '0.75rem',
                        padding: '0.6rem 0.8rem',
                        borderRadius: '12px',
                        fontSize: '0.9rem',
                        lineHeight: 1.45,
                        ...buildAssistantNoticeStyle(theme, assistantStatusNotice.tone),
                      }}
                    >
                      {assistantStatusNotice.text}
                    </div>
                  ) : null}
                  <MessageActions
                    messageContent={message.content}
                    sessionId={sessionId ?? undefined}
                    traceId={message.traceIds && message.traceIds.length > 0 ? message.traceIds[message.traceIds.length - 1] : undefined}
                    onFeedbackClick={() => onFeedbackClick(message.content, message.traceIds)}
                    reviewAndCurateTarget={message.reviewAndCurateTarget}
                    onReviewAndCurateOpened={(curationSessionId) => {
                      const messageId = message.id
                      if (!messageId) {
                        return
                      }

                      onReviewAndCurateOpened(messageId, curationSessionId)
                    }}
                  />
                </div>

                {hasEvidenceCard ? (
                  <EvidenceCard
                    evidenceRecords={message.evidenceRecords ?? []}
                    reviewAndCurateTarget={message.reviewAndCurateTarget}
                    onReviewAndCurateClick={handleEvidenceReviewAndCurateClick}
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
                onClick={() => onCopyMessage(message.content)}
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
  )
}

export default ChatMessageList
