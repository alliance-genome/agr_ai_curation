import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useChatStream } from './useChatStream'

describe('useChatStream shared lifecycle', () => {
  beforeEach(() => {
    vi.mocked(global.fetch).mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps one active assistant stream observable across hook remounts', async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
    const encoder = new TextEncoder()

    vi.mocked(global.fetch).mockResolvedValue(new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller
        },
      }),
      { status: 200 },
    ))

    const first = renderHook(() => useChatStream())

    act(() => {
      void first.result.current.sendMessage('hello', 'session-1', { turnId: 'turn-1' })
    })

    await waitFor(() => {
      expect(first.result.current.isLoading).toBe(true)
    })

    first.unmount()

    act(() => {
      streamController?.enqueue(encoder.encode(
        'data: {"type":"TEXT_MESSAGE_CONTENT","session_id":"session-1","turn_id":"turn-1","content":"hi"}\n\n',
      ))
    })

    const second = renderHook(() => useChatStream())

    await waitFor(() => {
      expect(second.result.current.isLoading).toBe(true)
      expect(second.result.current.events).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            type: 'TEXT_MESSAGE_CONTENT',
            session_id: 'session-1',
            turn_id: 'turn-1',
            content: 'hi',
          }),
        ]),
      )
    })

    act(() => {
      streamController?.close()
    })

    await waitFor(() => {
      expect(second.result.current.isLoading).toBe(false)
    })

    expect(global.fetch).toHaveBeenCalledTimes(1)
    second.result.current.clearEvents()
    second.unmount()
  })

  it('sends a stable client turn id for flow execution', async () => {
    const flowTurnId = '11111111-2222-3333-4444-555555555555'
    const randomUUIDSpy = vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(flowTurnId)
    vi.mocked(global.fetch).mockResolvedValue(new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.close()
        },
      }),
      { status: 200 },
    ))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.executeFlow('flow-1', 'session-1', 'document-1')
    })

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/chat/execute-flow',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          flow_id: 'flow-1',
          session_id: 'session-1',
          turn_id: flowTurnId,
          document_id: 'document-1',
          user_query: null,
        }),
      }),
    )

    result.current.clearEvents()
    unmount()
    randomUUIDSpy.mockRestore()
  })
})
