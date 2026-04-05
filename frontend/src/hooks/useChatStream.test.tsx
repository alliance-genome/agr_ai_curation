import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useChatStream } from './useChatStream'

const mockFetch = vi.fn()

function createStreamResponse(): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('data: [DONE]\n\n'))
      controller.close()
    },
  })

  return {
    ok: true,
    body,
  } as Response
}

describe('useChatStream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    global.fetch = mockFetch
  })

  it('sends base64 image data in the chat stream request body', async () => {
    mockFetch.mockResolvedValueOnce(createStreamResponse())

    const { result } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage({
        message: 'Please inspect this figure',
        image: {
          url: 'data:image/png;base64,ZmFrZS1pbWFnZQ==',
          filename: 'figure.png',
          mediaType: 'image/png',
          sizeBytes: 128,
        },
      }, 'session-123')
    })

    expect(mockFetch).toHaveBeenCalledWith('/api/chat/stream', expect.objectContaining({
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      signal: expect.any(AbortSignal),
    }))

    const [, init] = mockFetch.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({
      message: 'Please inspect this figure',
      image: {
        filename: 'figure.png',
        media_type: 'image/png',
        data_url: 'data:image/png;base64,ZmFrZS1pbWFnZQ==',
      },
      session_id: 'session-123',
    })
  })
})
