import {
  fetchChatHistoryDetail,
  type ChatHistoryDetailRequest,
  type ChatHistoryDetailResponse,
} from './chatHistoryApi'

/** Load the durable transcript in server chronological order, without a page cap. */
export async function fetchChatHistoryTranscript(
  request: ChatHistoryDetailRequest,
): Promise<ChatHistoryDetailResponse> {
  const messages: ChatHistoryDetailResponse['messages'] = []
  const seenCursors = new Set<string>()
  let nextCursor = request.messageCursor ?? null
  let firstPage: ChatHistoryDetailResponse | null = null

  while (true) {
    request.signal?.throwIfAborted()
    if (nextCursor) seenCursors.add(nextCursor)
    const page = await fetchChatHistoryDetail({ ...request, messageCursor: nextCursor })
    request.signal?.throwIfAborted()
    firstPage ??= page
    messages.push(...page.messages)

    if (!page.next_message_cursor) {
      return { ...firstPage, messages, next_message_cursor: null }
    }
    if (seenCursors.has(page.next_message_cursor)) {
      throw new Error(`Detected repeated chat history cursor for session ${request.sessionId}`)
    }
    nextCursor = page.next_message_cursor
  }
}
