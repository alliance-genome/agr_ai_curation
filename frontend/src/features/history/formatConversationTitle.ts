import type { ChatHistorySessionSummary } from '@/services/chatHistoryApi'

type ConversationTitleSession = Pick<ChatHistorySessionSummary, 'session_id' | 'title'>

export const UNTITLED_CONVERSATION_LABEL = 'Untitled conversation'

export function hasConversationTitle(session?: ConversationTitleSession | null): boolean {
  return Boolean(session?.title?.trim())
}

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

  return UNTITLED_CONVERSATION_LABEL
}
