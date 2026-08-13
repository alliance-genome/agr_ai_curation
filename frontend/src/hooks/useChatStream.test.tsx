import { useLayoutEffect } from 'react'
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CHAT_RUN_TERMINAL_EVENT, useChatStream } from './useChatStream'

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })

  return { promise, resolve, reject }
}

function sseResponse(events: readonly object[]): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        events.forEach((event) => {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
        })
        controller.close()
      },
    }),
    { status: 200 },
  )
}

describe('useChatStream shared lifecycle', () => {
  beforeEach(() => {
    vi.mocked(global.fetch).mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps the active session stream observable across route unmounts', async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
    const streamSignals: AbortSignal[] = []
    const encoder = new TextEncoder()

    vi.mocked(global.fetch).mockImplementation((_input, init) => {
      streamSignals.push(init?.signal as AbortSignal)
      return Promise.resolve(new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller
          },
        }),
        { status: 200 },
      ))
    })

    const first = renderHook(() => useChatStream('session-1'))
    let firstRun!: Promise<void>

    act(() => {
      firstRun = first.result.current.sendMessage('hello', 'session-1', { turnId: 'turn-1' })
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
    // Debbie's regression was Home -> Documents -> Home: route cleanup must not
    // abort a durable session run or erase the italic progress state while away.
    expect(streamSignals[0].aborted).toBe(false)

    act(() => {
      streamController?.enqueue(encoder.encode(
        'data: {"type":"TEXT_MESSAGE_CONTENT","session_id":"session-1","turn_id":"turn-1","content":"hi"}\n\n',
      ))
    })

    const second = renderHook(() => useChatStream('session-1'))

    expect(second.result.current.isLoading).toBe(true)
    expect(second.result.current.processedEventCount).toBe(1)
    await waitFor(() => {
      expect(second.result.current.events).toEqual(expect.arrayContaining([
        expect.objectContaining({
          type: 'TEXT_MESSAGE_CONTENT',
          session_id: 'session-1',
          turn_id: 'turn-1',
          content: 'hi',
        }),
      ]))
    })

    expect(global.fetch).toHaveBeenCalledTimes(1)

    act(() => streamController?.close())
    await act(async () => {
      await firstRun
    })
    expect(second.result.current.isLoading).toBe(false)

    second.result.current.clearEvents()
    second.unmount()
  })

  it('rechecks terminal state when a run stops during remount subscription', async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
    const streamSignals: AbortSignal[] = []

    vi.mocked(global.fetch).mockImplementation((input, init) => {
      if (input === '/api/chat/stop') {
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      streamSignals.push(init?.signal as AbortSignal)
      return Promise.resolve(new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller
          },
        }),
        { status: 200 },
      ))
    })

    const first = renderHook(() => useChatStream('session-race'))
    let firstRun!: Promise<void>
    act(() => {
      firstRun = first.result.current.sendMessage('hello', 'session-race')
    })
    await waitFor(() => expect(first.result.current.isLoading).toBe(true))
    first.unmount()

    function StopDuringRemount() {
      const stream = useChatStream('session-race')
      useLayoutEffect(() => {
        // This synchronous store update lands after render but before passive
        // subscriptions. useSyncExternalStore must still expose the terminal state.
        void stream.stopStream('session-race')
      }, [stream.stopStream])
      return <div data-testid="race-loading">{String(stream.isLoading)}</div>
    }

    const remount = render(<StopDuringRemount />)
    await waitFor(() => expect(screen.getByTestId('race-loading')).toHaveTextContent('false'))
    expect(streamSignals[0].aborted).toBe(true)

    act(() => streamController?.close())
    await firstRun
    remount.unmount()

    const cleanup = renderHook(() => useChatStream('session-race'))
    act(() => cleanup.result.current.clearEvents())
    cleanup.unmount()
  })

  it('cancels the owned run and retained events when the session changes', async () => {
    const encoder = new TextEncoder()
    const streamSignals: AbortSignal[] = []
    const streamControllers: ReadableStreamDefaultController<Uint8Array>[] = []

    vi.mocked(global.fetch).mockImplementation((_input, init) => {
      streamSignals.push(init?.signal as AbortSignal)
      return Promise.resolve(new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            streamControllers.push(controller)
          },
        }),
        { status: 200 },
      ))
    })

    const { result, rerender, unmount } = renderHook(
      ({ sessionId }) => useChatStream(sessionId),
      { initialProps: { sessionId: 'session-1' } },
    )
    let firstRun!: Promise<void>
    let secondRun!: Promise<void>

    act(() => {
      firstRun = result.current.sendMessage('first', 'session-1', { turnId: 'turn-1' })
    })
    await waitFor(() => expect(streamControllers).toHaveLength(1))

    rerender({ sessionId: 'session-2' })
    expect(streamSignals[0].aborted).toBe(true)
    expect(result.current.isLoading).toBe(false)
    expect(result.current.events).toEqual([])

    act(() => {
      secondRun = result.current.sendMessage('second', 'session-2', { turnId: 'turn-2' })
    })
    await waitFor(() => expect(streamControllers).toHaveLength(2))

    await act(async () => {
      await result.current.stopStream('session-1')
    })
    expect(streamSignals[1].aborted).toBe(false)
    expect(result.current.isLoading).toBe(true)

    act(() => {
      streamControllers[0].enqueue(encoder.encode(
        'data: {"type":"TEXT_MESSAGE_CONTENT","turn_id":"turn-1","content":"stale"}\n\n',
      ))
      streamControllers[0].close()
    })
    await act(async () => {
      await firstRun
    })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.events).toEqual([
      expect.objectContaining({ turn_id: 'turn-2' }),
    ])

    // Explicitly stop the replacement run so this test does not leave a
    // session-owned request alive; ordinary component unmount is non-terminal.
    await act(async () => {
      await result.current.stopStream('session-2')
    })
    expect(streamSignals[1].aborted).toBe(true)
    act(() => streamControllers[1].close())
    await secondRun
    result.current.clearEvents()
    unmount()
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

    vi.mocked(global.fetch).mockResolvedValue(sseResponse([
      {
        type: 'turn_completed',
        session_id: 'session-terminal',
        turn_id: 'turn-terminal',
        message: 'Chat turn completed.',
      },
    ]))

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

  it('uses the durable chat failure event as the terminal status', async () => {
    const terminalEvents: CustomEvent[] = []
    const listener = (event: Event) => terminalEvents.push(event as CustomEvent)
    window.addEventListener(CHAT_RUN_TERMINAL_EVENT, listener)
    vi.mocked(global.fetch).mockResolvedValue(sseResponse([
      {
        type: 'SUPERVISOR_ERROR',
        session_id: 'session-error',
        timestamp: '2026-06-30T00:00:00.000Z',
        details: { message: 'failed' },
      },
      {
        type: 'turn_failed',
        session_id: 'session-error',
        turn_id: 'turn-error',
        message: 'Chat turn failed.',
      },
    ]))

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

  it.each([
    {
      name: 'keeps a chat completion successful after a non-fatal specialist warning',
      runKind: 'chat',
      sessionId: 'session-warning',
      expectedStatus: 'completed',
      events: [
        {
          type: 'SPECIALIST_ERROR',
          details: { fatal: false, severity: 'warning' },
        },
        { type: 'turn_completed' },
      ],
    },
    {
      name: 'uses FLOW_FINISHED completion after a non-fatal specialist warning',
      runKind: 'flow',
      sessionId: 'flow-warning',
      expectedStatus: 'completed',
      events: [
        {
          type: 'SPECIALIST_ERROR',
          details: { fatal: false, severity: 'warning' },
        },
        { type: 'FLOW_FINISHED', status: 'completed' },
      ],
    },
    {
      name: 'uses a failed FLOW_FINISHED status without an earlier error event',
      runKind: 'flow',
      sessionId: 'flow-failed',
      expectedStatus: 'error',
      events: [{ type: 'FLOW_FINISHED', status: 'failed' }],
    },
  ] as const)('$name', async ({ events, expectedStatus, runKind, sessionId }) => {
    const terminalEvents: CustomEvent[] = []
    const listener = (event: Event) => terminalEvents.push(event as CustomEvent)
    window.addEventListener(CHAT_RUN_TERMINAL_EVENT, listener)
    vi.mocked(global.fetch).mockResolvedValue(sseResponse(events))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      if (runKind === 'chat') {
        await result.current.sendMessage('hello', sessionId)
      } else {
        await result.current.executeFlow('flow-1', sessionId)
      }
    })

    expect(terminalEvents).toHaveLength(1)
    expect(terminalEvents[0].detail).toEqual(
      expect.objectContaining({
        sessionId,
        runKind,
        status: expectedStatus,
      }),
    )

    result.current.clearEvents()
    unmount()
    window.removeEventListener(CHAT_RUN_TERMINAL_EVENT, listener)
  })

  it('keeps a replacement chat run owned when the stopped request rejects later', async () => {
    const firstFetch = deferred<Response>()
    const secondFetch = deferred<Response>()
    const streamSignals: AbortSignal[] = []

    vi.mocked(global.fetch).mockImplementation((input, init) => {
      if (input === '/api/chat/stop') {
        return Promise.resolve(new Response(null, { status: 200 }))
      }

      streamSignals.push(init?.signal as AbortSignal)
      return streamSignals.length === 1 ? firstFetch.promise : secondFetch.promise
    })

    const { result, unmount } = renderHook(() => useChatStream())
    let firstRun!: Promise<void>
    let secondRun!: Promise<void>

    act(() => {
      firstRun = result.current.sendMessage('first', 'session-1', { turnId: 'turn-1' })
    })
    await waitFor(() => expect(result.current.isLoading).toBe(true))

    await act(async () => {
      await result.current.stopStream('session-1')
    })
    expect(streamSignals[0].aborted).toBe(true)

    act(() => {
      secondRun = result.current.sendMessage('second', 'session-1', { turnId: 'turn-2' })
    })
    await waitFor(() => {
      expect(result.current.isLoading).toBe(true)
      expect(streamSignals).toHaveLength(2)
    })

    firstFetch.reject(new DOMException('stopped', 'AbortError'))
    await act(async () => {
      await firstRun
    })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.events).toEqual([
      expect.objectContaining({ turn_id: 'turn-2' }),
    ])

    await act(async () => {
      await result.current.stopStream('session-1')
    })
    expect(streamSignals[1].aborted).toBe(true)

    secondFetch.reject(new DOMException('stopped', 'AbortError'))
    await act(async () => {
      await secondRun
    })
    result.current.clearEvents()
    unmount()
  })

  it('ignores stale flow events and completion after a replacement flow starts', async () => {
    const encoder = new TextEncoder()
    const streamSignals: AbortSignal[] = []
    const streamControllers: ReadableStreamDefaultController<Uint8Array>[] = []

    vi.mocked(global.fetch).mockImplementation((input, init) => {
      if (input === '/api/chat/stop') {
        return Promise.resolve(new Response(null, { status: 200 }))
      }

      streamSignals.push(init?.signal as AbortSignal)
      return Promise.resolve(new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            streamControllers.push(controller)
          },
        }),
        { status: 200 },
      ))
    })

    const { result, unmount } = renderHook(() => useChatStream())
    let firstRun!: Promise<void>
    let secondRun!: Promise<void>

    act(() => {
      firstRun = result.current.executeFlow(
        'flow-1',
        'session-1',
        undefined,
        undefined,
        { turnId: 'flow-turn-1' },
      )
    })
    await waitFor(() => expect(streamControllers).toHaveLength(1))

    await act(async () => {
      await result.current.stopStream('session-1')
    })

    act(() => {
      secondRun = result.current.executeFlow(
        'flow-2',
        'session-1',
        undefined,
        undefined,
        { turnId: 'flow-turn-2' },
      )
    })
    await waitFor(() => {
      expect(result.current.isLoading).toBe(true)
      expect(streamControllers).toHaveLength(2)
    })

    act(() => {
      streamControllers[0].enqueue(encoder.encode(
        'data: {"type":"TEXT_MESSAGE_CONTENT","turn_id":"flow-turn-1","content":"stale"}\n\n',
      ))
      streamControllers[0].close()
    })
    await act(async () => {
      await firstRun
    })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.events).toEqual([
      expect.objectContaining({ turn_id: 'flow-turn-2' }),
    ])

    await act(async () => {
      await result.current.stopStream('session-1')
    })
    expect(streamSignals[0].aborted).toBe(true)
    expect(streamSignals[1].aborted).toBe(true)

    act(() => streamControllers[1].close())
    await act(async () => {
      await secondRun
    })
    result.current.clearEvents()
    unmount()
  })
})
