import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CHAT_RUN_TERMINAL_EVENT, useChatStream } from './useChatStream'

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

    act(() => {
      first.result.current.markEventsProcessed(
        first.result.current.eventStreamVersion,
        first.result.current.events.length,
      )
    })

    expect(first.result.current.processedEventCount).toBe(1)

    first.unmount()

    act(() => {
      streamController?.enqueue(encoder.encode(
        'data: {"type":"TEXT_MESSAGE_CONTENT","session_id":"session-1","turn_id":"turn-1","content":"hi"}\n\n',
      ))
    })

    const second = renderHook(() => useChatStream())

    await waitFor(() => {
      expect(second.result.current.isLoading).toBe(true)
      expect(second.result.current.processedEventCount).toBe(1)
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
    act(() => {
      second.result.current.clearEvents()
    })
    await waitFor(() => {
      expect(second.result.current.processedEventCount).toBe(0)
    })
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

  it('emits one terminal browser event when a chat stream completes', async () => {
    const terminalEvents: CustomEvent[] = []
    const listener = (event: Event) => terminalEvents.push(event as CustomEvent)
    window.addEventListener(CHAT_RUN_TERMINAL_EVENT, listener)

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
      await result.current.sendMessage('hello', 'session-terminal', { turnId: 'turn-terminal' })
    })

    expect(terminalEvents).toHaveLength(1)
    expect(terminalEvents[0].detail).toEqual(
      expect.objectContaining({
        sessionId: 'session-terminal',
        runKind: 'chat',
        status: 'completed',
      }),
    )

    result.current.clearEvents()
    unmount()
    window.removeEventListener(CHAT_RUN_TERMINAL_EVENT, listener)
  })

  it('marks streamed error events as terminal errors', async () => {
    const terminalEvents: CustomEvent[] = []
    const listener = (event: Event) => terminalEvents.push(event as CustomEvent)
    window.addEventListener(CHAT_RUN_TERMINAL_EVENT, listener)
    const encoder = new TextEncoder()

    vi.mocked(global.fetch).mockResolvedValue(new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(
            'data: {"type":"SUPERVISOR_ERROR","session_id":"session-error","timestamp":"2026-06-30T00:00:00.000Z","details":{"message":"failed"}}\n\n',
          ))
          controller.close()
        },
      }),
      { status: 200 },
    ))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('hello', 'session-error', { turnId: 'turn-error' })
    })

    expect(terminalEvents).toHaveLength(1)
    expect(terminalEvents[0].detail).toEqual(
      expect.objectContaining({
        sessionId: 'session-error',
        runKind: 'chat',
        status: 'error',
      }),
    )

    result.current.clearEvents()
    unmount()
    window.removeEventListener(CHAT_RUN_TERMINAL_EVENT, listener)
  })
})
