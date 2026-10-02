import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchChatHistoryDetail, type ChatHistoryDetailResponse } from './chatHistoryApi'
import { fetchChatHistoryTranscript } from './chatHistoryTranscript'

vi.mock('./chatHistoryApi', () => ({ fetchChatHistoryDetail: vi.fn() }))

function page(cursor: string | null): ChatHistoryDetailResponse {
  return {
    session: {
      session_id: 'session-1', chat_kind: 'assistant_chat',
      created_at: '2026-04-20T00:00:00Z', updated_at: '2026-04-20T00:00:00Z',
      recent_activity_at: '2026-04-20T00:00:00Z',
    },
    messages: [], message_limit: 100, next_message_cursor: cursor,
  }
}

describe('durable transcript loader', () => {
  beforeEach(() => vi.mocked(fetchChatHistoryDetail).mockReset())

  it.each([
    ['one', 'one'],
    ['one', 'two', 'one'],
  ])('rejects repeated cursors instead of returning a partial transcript (%s)', async (...cursors) => {
    cursors.forEach((cursor) => vi.mocked(fetchChatHistoryDetail).mockResolvedValueOnce(page(cursor)))
    await expect(fetchChatHistoryTranscript({ sessionId: 'session-1' })).rejects.toThrow('repeated chat history cursor')
    expect(fetchChatHistoryDetail).toHaveBeenCalledTimes(cursors.length)
  })

  it('detects a cycle back to the supplied starting cursor', async () => {
    vi.mocked(fetchChatHistoryDetail).mockResolvedValueOnce(page('start'))
    await expect(fetchChatHistoryTranscript({ sessionId: 'session-1', messageCursor: 'start' }))
      .rejects.toThrow('repeated chat history cursor')
    expect(fetchChatHistoryDetail).toHaveBeenCalledTimes(1)
  })

  it('cancels between pages even if the HTTP transport ignores abort', async () => {
    const controller = new AbortController()
    vi.mocked(fetchChatHistoryDetail).mockImplementationOnce(async (request) => {
      expect(request.signal).toBe(controller.signal)
      controller.abort()
      return page('next')
    })
    await expect(fetchChatHistoryTranscript({ sessionId: 'session-1', signal: controller.signal }))
      .rejects.toThrow()
    expect(fetchChatHistoryDetail).toHaveBeenCalledTimes(1)
  })

  it('surfaces a later-page failure without returning a partial transcript', async () => {
    vi.mocked(fetchChatHistoryDetail).mockResolvedValueOnce(page('next')).mockRejectedValueOnce(new Error('Page failed'))
    await expect(fetchChatHistoryTranscript({ sessionId: 'session-1' })).rejects.toThrow('Page failed')
  })
})
