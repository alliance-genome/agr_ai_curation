import {
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  type PersistedChatHistoryKind,
} from '@/services/chatHistoryApi'

export function getRestoreLabel(chatKind: PersistedChatHistoryKind): string {
  return chatKind === AGENT_STUDIO_CHAT_HISTORY_KIND
    ? 'Open in Agent Studio'
    : 'Resume chat'
}

export function getChatKindTagLabel(chatKind: PersistedChatHistoryKind): string {
  return chatKind === AGENT_STUDIO_CHAT_HISTORY_KIND ? 'Studio' : 'Assistant'
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return count === 1 ? singular : plural
}
