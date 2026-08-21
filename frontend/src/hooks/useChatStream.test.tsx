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

function sseResponse(events: readonly object[], eventIds?: readonly number[]): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        events.forEach((event, index) => {
          const eventId = eventIds?.[index]
          const idLine = eventId === undefined ? '' : `id: ${eventId}\n`
          controller.enqueue(encoder.encode(`${idLine}data: ${JSON.stringify(event)}\n\n`))
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
    vi.stubEnv('VITE_CHAT_STREAM_RECOVERY_DELAY_MS', '0')
    vi.stubEnv('VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS', '3')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
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

    act(() => {
      streamController?.enqueue(encoder.encode(
        'data: {"type":"turn_completed","session_id":"session-1","turn_id":"turn-1"}\n\n',
      ))
      streamController?.close()
    })
    await act(async () => {
      await firstRun
    })
    expect(second.result.current.isLoading).toBe(false)

    second.result.current.clearEvents()
    second.unmount()
  })

  it('re-observes the same chat turn after premature EOF and de-duplicates replay', async () => {
    const firstEvents = [
      { type: 'RUN_STARTED', session_id: 'session-recover', turn_id: 'turn-recover' },
      {
        type: 'TEXT_MESSAGE_CONTENT',
        session_id: 'session-recover',
        turn_id: 'turn-recover',
        content: 'part',
      },
    ]
    const replayEvents = [
      { ...firstEvents[0], timestamp: '2026-08-21T12:00:00Z' },
      firstEvents[1],
      {
        type: 'TEXT_MESSAGE_CONTENT',
        session_id: 'session-recover',
        turn_id: 'turn-recover',
        content: 'ial',
      },
      {
        type: 'turn_completed',
        session_id: 'session-recover',
        turn_id: 'turn-recover',
      },
    ]
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse(firstEvents, [0, 1]))
      .mockResolvedValueOnce(sseResponse(replayEvents, [0, 1, 2, 3]))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-recover', {
        turnId: 'turn-recover',
      })
    })

    expect(global.fetch).toHaveBeenCalledTimes(2)
    expect(vi.mocked(global.fetch).mock.calls[0][1]?.body).toBe(
      vi.mocked(global.fetch).mock.calls[1][1]?.body,
    )
    expect(vi.mocked(global.fetch).mock.calls[0][1]?.headers).not.toHaveProperty(
      'X-Observer-Recovery',
    )
    expect(vi.mocked(global.fetch).mock.calls[1][1]?.headers).toMatchObject({
      'X-Observer-Recovery': 'true',
    })
    expect(result.current.events.filter(event => event.type === 'RUN_STARTED')).toHaveLength(1)
    expect(result.current.events.filter(event => event.type === 'TEXT_MESSAGE_CONTENT')).toEqual([
      expect.objectContaining({ content: 'part' }),
      expect.objectContaining({ content: 'ial' }),
    ])
    expect(result.current.events.at(-1)?.type).toBe('turn_completed')
    expect(result.current.isLoading).toBe(false)
    expect(result.current.error).toBeNull()

    result.current.clearEvents()
    unmount()
  })

  it('surfaces an authoritative HTTP failure without attempting observer recovery', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(
      JSON.stringify({ detail: 'Session is active for a different user' }),
      {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      },
    ))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-forbidden', {
        turnId: 'turn-forbidden',
      })
    })

    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(result.current.error?.message).toContain('HTTP error! status: 403')
    expect(result.current.isLoading).toBe(false)

    result.current.clearEvents()
    unmount()
  })

  it('fails explicitly when the retained replay begins after the next expected event', async () => {
    const firstEvents = Array.from({ length: 6 }, (_value, index) => ({
      type: index === 0 ? 'RUN_STARTED' : 'TEXT_MESSAGE_CONTENT',
      session_id: 'session-gap',
      turn_id: 'turn-gap',
      ...(index === 0 ? {} : { content: `chunk-${index}` }),
    }))
    const retainedReplay = [
      {
        type: 'TEXT_MESSAGE_CONTENT',
        session_id: 'session-gap',
        turn_id: 'turn-gap',
        content: 'late chunk',
      },
      {
        type: 'turn_completed',
        session_id: 'session-gap',
        turn_id: 'turn-gap',
      },
    ]
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse(firstEvents, [0, 1, 2, 3, 4, 5]))
      .mockResolvedValueOnce(sseResponse(retainedReplay, [500, 501]))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-gap', {
        turnId: 'turn-gap',
      })
    })

    expect(global.fetch).toHaveBeenCalledTimes(2)
    expect(result.current.error?.message).toContain('Part of this response was lost')
    expect(result.current.events.filter(event => event.type === 'TEXT_MESSAGE_CONTENT')).toHaveLength(5)
    expect(result.current.events).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ content: 'late chunk' }),
    ]))
    expect(result.current.isLoading).toBe(false)

    result.current.clearEvents()
    unmount()
  })

  it('fails explicitly when recovery starts after event zero before any event was observed', async () => {
    vi.mocked(global.fetch)
      .mockRejectedValueOnce(new TypeError('initial connection reset'))
      .mockResolvedValueOnce(sseResponse([
        {
          type: 'TEXT_MESSAGE_CONTENT',
          session_id: 'session-initial-gap',
          turn_id: 'turn-initial-gap',
          content: 'retained suffix',
        },
        {
          type: 'turn_completed',
          session_id: 'session-initial-gap',
          turn_id: 'turn-initial-gap',
        },
      ], [25, 26]))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-initial-gap', {
        turnId: 'turn-initial-gap',
      })
    })

    expect(result.current.error?.message).toContain('Part of this response was lost')
    expect(result.current.events).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ content: 'retained suffix' }),
    ]))

    result.current.clearEvents()
    unmount()
  })

  it('preserves the initial transport error when recovery cannot observe a run', async () => {
    vi.mocked(global.fetch)
      .mockRejectedValueOnce(new TypeError('network unavailable'))
      .mockResolvedValue(new Response(JSON.stringify({
        detail: 'The original executable run is not observable on this worker',
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-network-error', {
        turnId: 'turn-network-error',
      })
    })

    expect(global.fetch).toHaveBeenCalledTimes(4)
    expect(result.current.error?.message).toContain('network unavailable')
    expect(result.current.error?.message).not.toContain('not observable')

    result.current.clearEvents()
    unmount()
  })

  it('renders new cursored events before a recovered live observer closes', async () => {
    const encoder = new TextEncoder()
    let recoveredController: ReadableStreamDefaultController<Uint8Array> | null = null
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse([{
        type: 'RUN_STARTED',
        session_id: 'session-live-recovery',
        turn_id: 'turn-live-recovery',
      }], [0]))
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          recoveredController = controller
        },
      }), { status: 200 }))

    const { result, unmount } = renderHook(() => useChatStream())
    let request!: Promise<void>
    act(() => {
      request = result.current.sendMessage('question', 'session-live-recovery', {
        turnId: 'turn-live-recovery',
      })
    })

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2))
    act(() => {
      recoveredController?.enqueue(encoder.encode(
        'id: 0\ndata: {"type":"RUN_STARTED","session_id":"session-live-recovery","turn_id":"turn-live-recovery"}\n\n'
        + 'id: 1\ndata: {"type":"TEXT_MESSAGE_CONTENT","session_id":"session-live-recovery","turn_id":"turn-live-recovery","content":"continued"}\n\n',
      ))
    })

    await waitFor(() => {
      expect(result.current.events).toEqual(expect.arrayContaining([
        expect.objectContaining({ content: 'continued' }),
      ]))
    })
    expect(result.current.isLoading).toBe(true)

    act(() => {
      recoveredController?.enqueue(encoder.encode(
        'id: 2\ndata: {"type":"turn_completed","session_id":"session-live-recovery","turn_id":"turn-live-recovery"}\n\n',
      ))
      recoveredController?.close()
    })
    await act(async () => await request)

    expect(result.current.isLoading).toBe(false)
    result.current.clearEvents()
    unmount()
  })

  it('de-duplicates a retained replay suffix after the backend evicts early events', async () => {
    const textEvents = Array.from({ length: 1001 }, (_value, index) => ({
      type: 'TEXT_MESSAGE_CONTENT',
      session_id: 'session-window',
      turn_id: 'turn-window',
      content: `chunk-${index}`,
    }))
    const firstEvents = [
      { type: 'RUN_STARTED', session_id: 'session-window', turn_id: 'turn-window' },
      {
        type: 'TOOL_COMPLETED',
        session_id: 'session-window',
        turn_id: 'turn-window',
        details: { message: 'first audit event' },
      },
      ...textEvents,
    ]
    const retainedSuffix = [
      // The default backend replay cap retains only these final 1000 events,
      // so recovery begins after RUN_STARTED, the first audit event, and chunk-0.
      ...firstEvents.slice(-1000),
      {
        type: 'TOOL_COMPLETED',
        session_id: 'session-window',
        turn_id: 'turn-window',
        details: { message: 'second audit event' },
      },
      {
        type: 'turn_completed',
        session_id: 'session-window',
        turn_id: 'turn-window',
      },
    ]
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse(
        firstEvents,
        firstEvents.map((_event, index) => index),
      ))
      .mockResolvedValueOnce(sseResponse(
        retainedSuffix,
        retainedSuffix.map((_event, index) => index + 3),
      ))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-window', {
        turnId: 'turn-window',
      })
    })

    expect(result.current.events.filter(event => event.type === 'RUN_STARTED')).toHaveLength(1)
    const renderedTextEvents = result.current.events.filter(
      event => event.type === 'TEXT_MESSAGE_CONTENT',
    )
    expect(renderedTextEvents).toHaveLength(1001)
    expect(renderedTextEvents[0]).toMatchObject({ content: 'chunk-0' })
    expect(renderedTextEvents.at(-1)).toMatchObject({ content: 'chunk-1000' })
    expect(result.current.events.filter(event => event.type === 'TOOL_COMPLETED')).toEqual([
      expect.objectContaining({ details: { message: 'first audit event' } }),
      expect.objectContaining({ details: { message: 'second audit event' } }),
    ])
    expect(result.current.events.at(-1)?.type).toBe('turn_completed')

    result.current.clearEvents()
    unmount()
  })

  it('uses replay cursors when repeated audit types and text chunks straddle eviction', async () => {
    const repeatedText = {
      type: 'TEXT_MESSAGE_CONTENT',
      session_id: 'session-repeated',
      turn_id: 'turn-repeated',
      content: 'same',
    }
    const audit = (message: string) => ({
      type: 'TOOL_COMPLETED',
      session_id: 'session-repeated',
      turn_id: 'turn-repeated',
      details: { message },
    })
    const firstEvents = [audit('A'), repeatedText, audit('B'), repeatedText]
    const retainedReplay = [
      audit('B'),
      repeatedText,
      audit('C'),
      repeatedText,
      {
        type: 'turn_completed',
        session_id: 'session-repeated',
        turn_id: 'turn-repeated',
      },
    ]
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse(firstEvents, [0, 1, 2, 3]))
      .mockResolvedValueOnce(sseResponse(retainedReplay, [2, 3, 4, 5, 6]))

    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-repeated', {
        turnId: 'turn-repeated',
      })
    })

    expect(result.current.events.filter(event => event.type === 'TOOL_COMPLETED')).toEqual([
      expect.objectContaining({ details: { message: 'A' } }),
      expect.objectContaining({ details: { message: 'B' } }),
      expect.objectContaining({ details: { message: 'C' } }),
    ])
    expect(result.current.events.filter(
      event => event.type === 'TEXT_MESSAGE_CONTENT',
    )).toHaveLength(3)
    expect(result.current.events.at(-1)?.type).toBe('turn_completed')

    result.current.clearEvents()
    unmount()
  })

  it('replaces repeated durable flow prefixes after recovery EOF', async () => {
    const runStarted = {
      type: 'RUN_STARTED',
      session_id: 'session-durable-flow',
      turn_id: 'turn-durable-flow',
    }
    const toolCompleted = {
      type: 'TOOL_COMPLETED',
      session_id: 'session-durable-flow',
      turn_id: 'turn-durable-flow',
      details: { message: 'durable audit event' },
    }
    const flowFinished = {
      type: 'FLOW_FINISHED',
      session_id: 'session-durable-flow',
      turn_id: 'turn-durable-flow',
      status: 'completed',
    }
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse([runStarted], [0]))
      .mockResolvedValueOnce(sseResponse([runStarted, toolCompleted]))
      .mockResolvedValueOnce(sseResponse([runStarted, toolCompleted, flowFinished]))

    const { result, unmount } = renderHook(() => useChatStream())
    const initialVersion = result.current.eventStreamVersion
    const initialReconciliationVersion = result.current.durableReconciliationVersion

    await act(async () => {
      await result.current.executeFlow(
        'flow-1',
        'session-durable-flow',
        undefined,
        undefined,
        { turnId: 'turn-durable-flow' },
      )
    })

    expect(global.fetch).toHaveBeenCalledTimes(3)
    expect(result.current.events.filter(event => event.type === 'RUN_STARTED')).toHaveLength(1)
    expect(result.current.events.filter(event => event.type === 'TOOL_COMPLETED')).toEqual([
      expect.objectContaining({ details: { message: 'durable audit event' } }),
    ])
    expect(result.current.events.filter(event => event.type === 'FLOW_FINISHED')).toHaveLength(1)
    expect(result.current.events.at(-1)).toMatchObject({
      type: 'FLOW_FINISHED',
      status: 'completed',
    })
    expect(result.current.eventStreamVersion).toBe(initialVersion + 3)
    expect(result.current.durableReconciliationVersion).toBe(
      initialReconciliationVersion + 2,
    )
    expect(result.current.isLoading).toBe(false)
    expect(result.current.error).toBeNull()

    result.current.clearEvents()
    unmount()
  })

  it('keeps running state active while a detached observer is recovering', async () => {
    const recoveredResponse = deferred<Response>()
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse([{
        type: 'RUN_STARTED',
        session_id: 'session-recovering',
        turn_id: 'turn-recovering',
      }], [0]))
      .mockImplementationOnce(() => recoveredResponse.promise)
    const { result, unmount } = renderHook(() => useChatStream())
    let request!: Promise<void>

    act(() => {
      request = result.current.sendMessage('question', 'session-recovering', {
        turnId: 'turn-recovering',
      })
    })

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2))
    expect(result.current.isLoading).toBe(true)
    expect(result.current.error).toBeNull()

    recoveredResponse.resolve(sseResponse([
      {
        type: 'RUN_STARTED',
        session_id: 'session-recovering',
        turn_id: 'turn-recovering',
      },
      {
        type: 'turn_completed',
        session_id: 'session-recovering',
        turn_id: 'turn-recovering',
      },
    ], [0, 1]))
    await act(async () => await request)

    expect(result.current.isLoading).toBe(false)
    expect(result.current.events.filter(event => event.type === 'RUN_STARTED')).toHaveLength(1)

    result.current.clearEvents()
    unmount()
  })

  it('surfaces an explicit error only after same-turn recovery is exhausted', async () => {
    vi.stubEnv('VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS', '1')
    vi.mocked(global.fetch).mockResolvedValue(sseResponse([]))
    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-exhausted', {
        turnId: 'turn-exhausted',
      })
    })

    expect(global.fetch).toHaveBeenCalledTimes(2)
    expect(result.current.error?.message).toContain('before the run completed')
    expect(result.current.isLoading).toBe(false)

    result.current.clearEvents()
    unmount()
  })

  it('surfaces the durable-observation detail when recovery 409s until exhaustion', async () => {
    vi.stubEnv('VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS', '1')
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(sseResponse([]))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: 'The original executable run is not observable on this worker yet.',
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }))
    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-pending', {
        turnId: 'turn-pending',
      })
    })

    expect(global.fetch).toHaveBeenCalledTimes(2)
    expect(result.current.error?.message).toContain('HTTP error! status: 409')
    expect(result.current.error?.message).toContain('not observable on this worker yet')
    expect(result.current.isLoading).toBe(false)

    result.current.clearEvents()
    unmount()
  })

  it('uses the documented recovery default when the attempts env value is empty', async () => {
    vi.stubEnv('VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS', '')
    vi.mocked(global.fetch).mockResolvedValue(sseResponse([]))
    const { result, unmount } = renderHook(() => useChatStream())

    await act(async () => {
      await result.current.sendMessage('question', 'session-default-attempts', {
        turnId: 'turn-default-attempts',
      })
    })

    expect(global.fetch).toHaveBeenCalledTimes(4)
    expect(result.current.error?.message).toContain('before the run completed')

    result.current.clearEvents()
    unmount()
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
