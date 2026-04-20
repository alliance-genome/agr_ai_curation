import type { ChatHistorySessionSummary } from '@/services/chatHistoryApi'

type ConversationTitleSession = Pick<ChatHistorySessionSummary, 'session_id' | 'title'>

export default function formatConversationTitle(
  session?: ConversationTitleSession | null,
): string {
  if (!session) {
    return ''
  }

  const trimmedTitle = session.title?.trim()
  if (trimmedTitle) {
    return trimmedTitle
  }

  return `Conversation ${session.session_id.slice(0, 8)}`
}
