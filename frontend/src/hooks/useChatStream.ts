/**
 * useChatStream Hook
 *
 * Shared hook for handling SSE chat streaming.
 * Extracts SSE handling logic from Chat component to enable reuse by AuditPanel.
 *
 * This hook encapsulates:
 * - POST fetch to /api/chat/stream
 * - SSE event parsing from response.body reader
 * - Event stream state management
 *
 * Note: Uses POST fetch with ReadableStream, NOT EventSource API
 */

import { useEffect, useCallback, useSyncExternalStore } from 'react'
import { debug } from '@/utils/env'

export interface SSEEvent {
  type: string
  // SSE payloads are intentionally open-ended because backend event shapes vary by tool and lane.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [key: string]: any
}

export interface SendChatMessageOptions {
  turnId?: string
}

export interface ExecuteFlowOptions {
  turnId?: string
}

export type ChatRunTerminalStatus = 'completed' | 'error'
export type ChatRunKind = 'chat' | 'flow'

const CHAT_TERMINAL_EVENT_TYPES = new Set([
  'turn_completed',
  'turn_interrupted',
  'turn_failed',
  'turn_save_failed',
  'session_gone',
])

export interface ChatRunTerminalEventDetail {
  sessionId: string
  runKind: ChatRunKind
  status: ChatRunTerminalStatus
  eventStreamVersion: number
}

export const CHAT_RUN_TERMINAL_EVENT = 'agr-chat-run-terminal'

export interface UseChatStreamReturn {
  /**
   * All SSE events received in this session
   */
  events: SSEEvent[]

  /**
   * Version for the current retained event stream. Increments when the stream is replaced.
   */
  eventStreamVersion: number

  /**
   * Number of events the chat renderer has already consumed for this stream version.
   */
  processedEventCount: number

  /**
   * Whether a stream request is currently in progress
   */
  isLoading: boolean

  /**
   * Send a message and start receiving SSE events
   */
  sendMessage: (
    message: string,
    sessionId: string,
    options?: SendChatMessageOptions,
  ) => Promise<void>

  /**
   * Execute a curation flow with streaming response
   */
  executeFlow: (
    flowId: string,
    sessionId: string,
    documentId?: string,
    userQuery?: string,
    options?: ExecuteFlowOptions,
  ) => Promise<void>

  /**
   * Last error encountered during streaming
   */
  error: Error | null

  /**
   * Clear all received events
   */
  clearEvents: () => void

  /**
   * Record how many retained events the chat renderer has consumed.
   */
  markEventsProcessed: (eventStreamVersion: number, count: number) => void

  /**
   * Abort the current stream (if any)
   */
  stopStream: (sessionId: string) => Promise<void>
}

interface SharedChatStreamState {
  events: SSEEvent[]
  eventStreamVersion: number
  processedEventCount: number
  isLoading: boolean
  error: Error | null
}

interface ActiveStreamRun {
  runId: number
  controller: AbortController
  sessionId: string
  observedEvents: SSEEvent[]
}

const sharedListeners = new Set<() => void>()
let sharedState: SharedChatStreamState = {
  events: [],
  eventStreamVersion: 0,
  processedEventCount: 0,
  isLoading: false,
  error: null,
}
let nextRunId = 0
let activeStreamRun: ActiveStreamRun | null = null
let retainedEventSessionId: string | null = null

function emitSharedState(nextState: Partial<SharedChatStreamState>) {
  sharedState = { ...sharedState, ...nextState }
  sharedListeners.forEach((listener) => listener())
}

function subscribeSharedState(listener: () => void): () => void {
  sharedListeners.add(listener)
  return () => sharedListeners.delete(listener)
}

function getSharedStateSnapshot(): SharedChatStreamState {
  return sharedState
}

function updateSharedEvents(updater: (events: SSEEvent[]) => SSEEvent[]) {
  emitSharedState({ events: updater(sharedState.events) })
}

function replaceSharedEvents(events: SSEEvent[]) {
  emitSharedState({
    events,
    eventStreamVersion: sharedState.eventStreamVersion + 1,
    processedEventCount: 0,
  })
}

function buildClientTurnId(): string {
  return globalThis.crypto.randomUUID()
}

function getRunTerminalStatus(
  events: SSEEvent[],
  runKind: ChatRunKind,
): ChatRunTerminalStatus {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]

    if (runKind === 'flow' && event.type === 'FLOW_FINISHED') {
      return event.status === 'completed' ? 'completed' : 'error'
    }

    if (runKind === 'chat' && CHAT_TERMINAL_EVENT_TYPES.has(event.type)) {
      return event.type === 'turn_completed' ? 'completed' : 'error'
    }
  }

  return 'error'
}

function isAuthoritativeTerminal(event: SSEEvent, runKind: ChatRunKind): boolean {
  return runKind === 'flow'
    ? event.type === 'FLOW_FINISHED' || event.type === 'RUN_ERROR'
    : CHAT_TERMINAL_EVENT_TYPES.has(event.type)
}

function getRecoveryMaxAttempts(): number {
  const configured = Number(import.meta.env.VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS ?? 3)
  return Number.isFinite(configured) ? Math.max(0, Math.floor(configured)) : 3
}

function getRecoveryDelayMs(): number {
  const configured = Number(import.meta.env.VITE_CHAT_STREAM_RECOVERY_DELAY_MS ?? 1000)
  return Number.isFinite(configured) ? Math.max(0, configured) : 1000
}

function sameReplayEvent(left: SSEEvent, right: SSEEvent): boolean {
  if (JSON.stringify(left) === JSON.stringify(right)) return true
  if (left.type !== right.type) return false

  // Durable transcript reconstruction can change transport metadata such as a
  // timestamp while preserving the semantic audit event at the same prefix
  // position. Text-bearing events must still compare their actual payload.
  if (left.type === 'TEXT_MESSAGE_CONTENT') {
    return (left.content ?? left.delta) === (right.content ?? right.delta)
  }
  if (left.type === 'CHAT_OUTPUT_READY') {
    return (left.details?.output ?? left.details?.output_preview)
      === (right.details?.output ?? right.details?.output_preview)
  }
  return true
}

function findReplayOverlap(observed: SSEEvent[], replay: SSEEvent[]): number {
  const maximum = Math.min(observed.length, replay.length)

  for (let overlap = maximum; overlap > 0; overlap -= 1) {
    const observedStart = observed.length - overlap
    let matches = true
    for (let index = 0; index < overlap; index += 1) {
      if (!sameReplayEvent(observed[observedStart + index], replay[index])) {
        matches = false
        break
      }
    }
    if (matches) return overlap
  }

  return 0
}

function reconcileReplay(run: ActiveStreamRun, replay: SSEEvent[]) {
  if (replay.length === 0) return

  const overlap = findReplayOverlap(run.observedEvents, replay)
  const reconcilesDurableTranscript = overlap === 0 && run.observedEvents.length > 0
  let firstTextSeen = false
  const nextEvents = replay.slice(overlap).map((event) => {
    if (
      reconcilesDurableTranscript
      && !firstTextSeen
      && event.type === 'TEXT_MESSAGE_CONTENT'
    ) {
      firstTextSeen = true
      return { ...event, observer_reconcile: true }
    }
    return event
  })

  if (reconcilesDurableTranscript) {
    run.observedEvents.splice(0, run.observedEvents.length, ...replay)
  } else {
    run.observedEvents.push(...replay.slice(overlap))
  }
  if (ownsActiveRun(run) && nextEvents.length > 0) {
    updateSharedEvents(previous => [...previous, ...nextEvents])
  }
}

function waitForRecovery(signal: AbortSignal): Promise<void> {
  const delayMs = getRecoveryDelayMs()
  if (delayMs === 0) return Promise.resolve()

  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeout)
      reject(new DOMException('Stream aborted', 'AbortError'))
    }
    const timeout = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, delayMs)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

async function observeRun(
  run: ActiveStreamRun,
  runKind: ChatRunKind,
  url: string,
  requestBody: object,
): Promise<void> {
  let recoveryAttempts = 0

  while (ownsActiveRun(run)) {
    let terminalSeen = false
    const recovering = recoveryAttempts > 0
    const replay: SSEEvent[] = []

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(recovering ? { 'X-Observer-Recovery': 'true' } : {}),
        },
        body: JSON.stringify(requestBody),
        signal: run.controller.signal,
      })
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)

      const reader = response.body?.getReader()
      if (!reader) throw new Error('Response body is not readable')

      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        if (!ownsActiveRun(run)) return

        buffer += decoder.decode(value, { stream: true })
        let boundaryIndex: number
        while ((boundaryIndex = buffer.indexOf('\n\n')) !== -1) {
          const eventData = buffer.substring(0, boundaryIndex)
          buffer = buffer.substring(boundaryIndex + 2)

          for (const line of eventData.split('\n')) {
            if (!line.startsWith('data: ')) continue
            const data = line.slice(6)
            if (data === '[DONE]') continue

            try {
              const parsed: SSEEvent = JSON.parse(data)
              terminalSeen ||= isAuthoritativeTerminal(parsed, runKind)

              if (recovering) {
                replay.push(parsed)
                continue
              }

              run.observedEvents.push(parsed)
              debug.log('🔍 [useChatStream] Received SSE event:', parsed.type, parsed)
              if (ownsActiveRun(run)) updateSharedEvents(prev => [...prev, parsed])
            } catch (parseError) {
              console.error('Failed to parse SSE event:', parseError, data)
            }
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') throw err
      debug.log('[useChatStream] Observer detached with error; recovering same turn', err)
    }

    if (recovering) reconcileReplay(run, replay)

    if (!ownsActiveRun(run)) return
    if (terminalSeen) {
      const terminalStatus = getRunTerminalStatus(sharedState.events, runKind)
      const eventStreamVersion = sharedState.eventStreamVersion
      releaseStreamRun(run)
      emitChatRunTerminal({
        sessionId: run.sessionId,
        runKind,
        status: terminalStatus,
        eventStreamVersion,
      })
      return
    }

    if (recoveryAttempts >= getRecoveryMaxAttempts()) {
      throw new Error(
        'The response connection ended before the run completed. Reopen this chat to retry recovery.',
      )
    }
    recoveryAttempts += 1
    await waitForRecovery(run.controller.signal)
  }
}

function emitChatRunTerminal(detail: ChatRunTerminalEventDetail) {
  window.dispatchEvent(new CustomEvent<ChatRunTerminalEventDetail>(CHAT_RUN_TERMINAL_EVENT, {
    detail,
  }))
}

function startStreamRun(
  sessionId: string,
): ActiveStreamRun | null {
  // Restart policy: reject starts while a run is active. A user stop releases
  // that run synchronously, so a replacement need not wait for stale work to settle.
  if (activeStreamRun) {
    return null
  }

  const run = {
    runId: ++nextRunId,
    controller: new AbortController(),
    sessionId,
    observedEvents: [],
  }
  activeStreamRun = run
  emitSharedState({ isLoading: true, error: null })
  return run
}

function replaceRunEvents(
  run: ActiveStreamRun,
  events: SSEEvent[],
) {
  retainedEventSessionId = run.sessionId
  replaceSharedEvents(events)
}

function ownsActiveRun(run: ActiveStreamRun): boolean {
  return activeStreamRun?.runId === run.runId
}

function releaseStreamRun(
  run: ActiveStreamRun,
  nextState: Partial<SharedChatStreamState> = {},
): boolean {
  if (!ownsActiveRun(run)) {
    return false
  }

  activeStreamRun = null
  emitSharedState({ ...nextState, isLoading: false })
  return true
}

function cleanupStreamSession(sessionId: string) {
  const run = activeStreamRun
  if (run?.sessionId === sessionId) {
    run.controller.abort()
    releaseStreamRun(run)
  }

  if (retainedEventSessionId === sessionId) {
    retainedEventSessionId = null
    replaceSharedEvents([])
    emitSharedState({ error: null })
  }
}

/**
 * Hook for managing chat SSE stream
 *
 * The durable chat session owns its stream. Route unmounts leave the request
 * alive so Home can observe the same run after navigation; selecting a different
 * session still aborts and clears the prior session before it can leak state.
 *
 * @param activeSessionId Session whose stream state should be observed.
 * @returns Stream state and control functions
 */
export function useChatStream(activeSessionId?: string | null): UseChatStreamReturn {
  // The request may finish between a route remount's render and subscription.
  // React's external-store contract rechecks the snapshot across that window.
  const snapshot = useSyncExternalStore(
    subscribeSharedState,
    getSharedStateSnapshot,
    getSharedStateSnapshot,
  )

  useEffect(() => {
    if (!activeSessionId) {
      return
    }

    const priorSessionId = activeStreamRun?.sessionId ?? retainedEventSessionId
    if (priorSessionId && priorSessionId !== activeSessionId) {
      cleanupStreamSession(priorSessionId)
    }
  }, [activeSessionId])

  const clearEvents = useCallback(() => {
    retainedEventSessionId = null
    replaceSharedEvents([])
    emitSharedState({ error: null })
  }, [])

  const markEventsProcessed = useCallback((eventStreamVersion: number, count: number) => {
    if (eventStreamVersion !== sharedState.eventStreamVersion) {
      return
    }

    const nextCount = Math.min(Math.max(0, count), sharedState.events.length)
    if (nextCount <= sharedState.processedEventCount) {
      return
    }

    emitSharedState({ processedEventCount: nextCount })
  }, [])

  const stopStream = useCallback(async (sessionId: string) => {
    const run = activeStreamRun
    if (!run || run.sessionId !== sessionId) {
      return
    }

    run.controller.abort()
    if (!releaseStreamRun(run)) {
      return
    }

    // Emit a synthetic event so Audit/Chat can show a stop notice even without SSE
    updateSharedEvents(prev => [
      ...prev,
      {
        type: 'STOP_CONFIRMED',
        session_id: sessionId,
        details: { message: 'Interaction stopped by user' },
        timestamp: new Date().toISOString()
      }
    ])
    try {
      await fetch('/api/chat/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId })
      })
    } catch (err) {
      console.error('Failed to send stop request', err)
    }
  }, [])

  const sendMessage = useCallback(async (
    message: string,
    sessionId: string,
    options?: SendChatMessageOptions,
  ) => {
    if (!message.trim()) {
      console.warn('Cannot send empty message')
      return
    }

    if (!sessionId) {
      const err = new Error('No session ID available')
      emitSharedState({ error: err })
      console.error(err)
      return
    }

    const turnId = options?.turnId ?? buildClientTurnId()
    const run = startStreamRun(sessionId)
    if (!run) {
      console.warn('Cannot start a new chat message while another stream is active')
      return
    }

    // Start each run with a fresh stream so consumers do not have to reconcile
    // stale events from prior turns before processing the new audit trail.
    replaceRunEvents(run, [
      {
        type: 'AGENT_GENERATING',
        session_id: sessionId,
        turn_id: turnId,
        timestamp: new Date().toISOString(),
        details: {
          agentRole: 'System',
          agentDisplayName: 'System',
          message: 'Initializing AI agents'
        }
      }
    ])

    try {
      await observeRun(run, 'chat', '/api/chat/stream', {
        message,
        session_id: sessionId,
        turn_id: turnId,
      })
    } catch (err) {
      if (!ownsActiveRun(run)) return

      // Ignore abort errors (user cancelled)
      if (err instanceof Error && err.name === 'AbortError') {
        debug.log('Stream aborted by user')
        releaseStreamRun(run)
        return
      }

      const error = err instanceof Error ? err : new Error('Unknown error during streaming')
      releaseStreamRun(run, { error })
      console.error('Error in chat stream:', error)
    } finally {
      releaseStreamRun(run)
    }
  }, [])

  /**
   * Execute a curation flow with SSE streaming
   */
  const executeFlow = useCallback(async (
    flowId: string,
    sessionId: string,
    documentId?: string,
    userQuery?: string,
    options?: ExecuteFlowOptions,
  ) => {
    if (!sessionId) {
      const err = new Error('No session ID available')
      emitSharedState({ error: err })
      console.error(err)
      return
    }

    const turnId = options?.turnId ?? buildClientTurnId()
    const run = startStreamRun(sessionId)
    if (!run) {
      console.warn('Cannot start a new flow execution while another stream is active')
      return
    }

    // Start each flow execution with a fresh stream for the same reason as
    // normal chat sends: right-panel consumers should only process this run.
    replaceRunEvents(run, [
      {
        type: 'AGENT_GENERATING',
        session_id: sessionId,
        turn_id: turnId,
        timestamp: new Date().toISOString(),
        details: {
          agentRole: 'System',
          agentDisplayName: 'Flow Executor',
          message: 'Starting curation flow'
        }
      }
    ])

    try {
      await observeRun(run, 'flow', '/api/chat/execute-flow', {
        flow_id: flowId,
        session_id: sessionId,
        turn_id: turnId,
        document_id: documentId || null,
        user_query: userQuery || null,
      })
    } catch (err) {
      if (!ownsActiveRun(run)) return

      if (err instanceof Error && err.name === 'AbortError') {
        debug.log('Flow execution aborted by user')
        releaseStreamRun(run)
        return
      }
      const error = err instanceof Error ? err : new Error('Unknown error during flow execution')
      releaseStreamRun(run, { error })
      console.error('Error in flow execution:', error)
    } finally {
      releaseStreamRun(run)
    }
  }, [])

  return {
    events: snapshot.events,
    eventStreamVersion: snapshot.eventStreamVersion,
    processedEventCount: snapshot.processedEventCount,
    isLoading: snapshot.isLoading,
    sendMessage,
    executeFlow,
    error: snapshot.error,
    clearEvents,
    markEventsProcessed,
    stopStream
  }
}
