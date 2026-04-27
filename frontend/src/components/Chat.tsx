import { useMemo } from 'react'

import { useTheme } from '@mui/material/styles'

import PrepScopeConfirmationDialog from '@/features/curation/components/PrepScopeConfirmationDialog'

import ChatComposer from './Chat/ChatComposer'
import ChatHeader from './Chat/ChatHeader'
import ChatMessageList from './Chat/ChatMessageList'
import ChatNoticeBars from './Chat/ChatNoticeBars'
import FeedbackDialog from './Chat/FeedbackDialog'
import { buildChatCssVariables } from './Chat/chatStyles'
import type { ChatProps } from './Chat/types'
import { useChatController } from './Chat/useChatController'

function Chat(props: ChatProps) {
  const theme = useTheme()
  const chatCssVariables = useMemo(() => buildChatCssVariables(theme), [theme])
  const controller = useChatController(props)

  return (
    <div
      style={{
        ...chatCssVariables,
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
      <ChatHeader
        activeDocument={controller.activeDocument}
        conversationStatus={controller.conversationStatus}
        normalizedSessionId={controller.normalizedSessionId}
        sessionIdCopied={controller.sessionIdCopied}
        isResetting={controller.isResetting}
        isUnloadingPDF={controller.isUnloadingPDF}
        prepButtonDisabled={controller.prepButtonDisabled}
        prepButtonLabel={controller.prepButtonLabel}
        onCopySessionId={controller.handleCopySessionId}
        onResetConversation={controller.handleResetConversation}
        onUnloadPDF={controller.handleUnloadPDF}
        onOpenPrepDialog={controller.handleOpenPrepDialog}
      />

      <ChatNoticeBars
        prepStatus={controller.prepStatus}
        limitNotices={controller.limitNotices}
        refinePrompt={controller.refinePrompt}
        refineText={controller.refineText}
        weaviateConnected={controller.weaviateConnected}
        showCurationDbWarning={controller.showCurationDbWarning}
        onRefineTextChange={controller.setRefineText}
        onRefineSubmit={controller.handleRefineSubmit}
        onSendQuickMessage={controller.handleSendQuickMessage}
        onDismissRefinePrompt={controller.handleDismissRefinePrompt}
      />

      <ChatMessageList
        messages={controller.messages}
        isLoading={controller.isLoading}
        progressMessage={controller.progressMessage}
        messagesEndRef={controller.messagesEndRef}
        chatCssVariables={chatCssVariables}
        sessionId={controller.propSessionId}
        onCopyMessage={controller.handleCopyMessage}
        onFeedbackClick={controller.handleFeedbackClick}
        onOpenCurationWorkspace={controller.handleOpenCurationWorkspace}
        onReviewAndCurateOpened={controller.handleReviewAndCurateOpened}
        onUnsupportedEvidenceReview={controller.handleUnsupportedEvidenceReview}
      />

      <ChatComposer
        textareaRef={controller.textareaRef}
        inputMessage={controller.inputMessage}
        isLoading={controller.isLoading}
        onInputChange={controller.handleInputChange}
        onKeyPress={controller.handleKeyPress}
        onSendMessage={controller.handleSendMessage}
      />

      <FeedbackDialog
        open={controller.feedbackDialogOpen}
        onClose={controller.handleFeedbackDialogClose}
        sessionId={controller.feedbackSessionId}
        traceIds={controller.feedbackMessageData?.traceIds || []}
        curatorId={controller.user?.email || 'unknown@example.com'}
        onSubmit={controller.handleFeedbackSubmit}
      />

      <PrepScopeConfirmationDialog
        open={controller.prepDialogOpen}
        preview={controller.effectivePrepPreview}
        visibleConversationMessageCount={controller.visibleConversationMessageCount}
        supplementalNotice={controller.prepSupplementalNotice}
        loading={controller.isLoadingPrepPreview}
        submitting={controller.isPreparingCuration}
        error={controller.prepDialogError}
        onClose={controller.handleClosePrepDialog}
        onConfirm={controller.handleConfirmPrep}
      />
    </div>
  )
}

export default Chat
