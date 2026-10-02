import { getEnvInt } from '@/utils/env'
import {
  fetchChatHistoryDetail,
  type ChatHistoryDetailRequest,
  type ChatHistoryDetailResponse,
} from '@/services/chatHistoryApi'

// Largest page the chat history detail endpoint accepts (`message_limit` le=200).
export const CHAT_HISTORY_TRANSCRIPT_PAGE_SIZE = 200
const DEFAULT_CHAT_HISTORY_TRANSCRIPT_MAX_PAGES = 50

export function getChatHistoryTranscriptMaxPages(): number {
  return Math.max(
    1,
    getEnvInt(
      ['VITE_AI_CURATION_CHAT_TRANSCRIPT_MAX_PAGES', 'AI_CURATION_CHAT_TRANSCRIPT_MAX_PAGES'],
      DEFAULT_CHAT_HISTORY_TRANSCRIPT_MAX_PAGES,
    ),
  )
}

export class ChatHistoryTranscriptLimitError extends Error {
  readonly maxPages: number
  readonly pageSize: number

  constructor(maxPages: number, pageSize: number) {
    super(
      `This chat is too long to restore in full: it has more than ${maxPages * pageSize} messages, `
      + 'which is the most this page can load. Start a new chat to keep working, or ask an '
      + 'administrator to raise the chat restore limit.',
    )
    this.name = 'ChatHistoryTranscriptLimitError'
    this.maxPages = maxPages
    this.pageSize = pageSize
  }
}

/**
 * Loads a durable chat transcript by following `next_message_cursor` until the
 * server reports no more pages. Messages keep the server's chronological order.
 * Throws instead of returning a partial transcript when a page fails, a cursor
 * repeats, or the configured page limit is reached.
 */
export async function fetchChatHistoryTranscript(
  request: ChatHistoryDetailRequest,
): Promise<ChatHistoryDetailResponse> {
  const sessionId = request.sessionId.trim()
  const messageLimit = request.messageLimit ?? CHAT_HISTORY_TRANSCRIPT_PAGE_SIZE
  const maxPages = getChatHistoryTranscriptMaxPages()
  const messages: ChatHistoryDetailResponse['messages'] = []
  const seenCursors = new Set<string>()

  let nextCursor = request.messageCursor ?? null
  let detailResponse: ChatHistoryDetailResponse | null = null

  for (let pageCount = 0; pageCount < maxPages; pageCount += 1) {
    const page = await fetchChatHistoryDetail({
      sessionId,
      chatKind: request.chatKind,
      messageLimit,
      messageCursor: nextCursor,
      signal: request.signal,
    })

    if (!detailResponse) {
      detailResponse = page
    }

    messages.push(...page.messages)

    if (!page.next_message_cursor) {
      return {
        ...page,
        session: detailResponse.session,
        active_document: detailResponse.active_document,
        messages,
        next_message_cursor: null,
      }
    }

    if (seenCursors.has(page.next_message_cursor)) {
      throw new Error(`Detected repeated chat history cursor for session ${sessionId}`)
    }

    seenCursors.add(page.next_message_cursor)
    nextCursor = page.next_message_cursor
  }

  throw new ChatHistoryTranscriptLimitError(maxPages, messageLimit)
}
