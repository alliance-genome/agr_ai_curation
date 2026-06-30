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

import { useState, useEffect, useCallback, useRef } from 'react'
import { debug } from '@/utils/env'
import { getStreamEventSessionId } from '../lib/streamEventSession'

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

export type ChatRunTerminalStatus = 'idle' | 'finished' | 'stopped' | 'error'

export interface ChatRunActivitySummary {
  isLoading: boolean
  error: Error | null
  latestSessionId: string | null
  terminalStatus: ChatRunTerminalStatus
  eventStreamVersion: number
}

interface SharedChatStreamState {
  events: SSEEvent[]
  eventStreamVersion: number
  processedEventCount: number
  isLoading: boolean
  error: Error | null
}

const sharedListeners = new Set<() => void>()
let sharedState: SharedChatStreamState = {
  events: [],
  eventStreamVersion: 0,
  processedEventCount: 0,
  isLoading: false,
  error: null,
}
let sharedAbortController: AbortController | null = null
const CHAT_RUN_TERMINAL_ERROR_EVENT_TYPES = new Set([
  'RUN_ERROR',
  'SUPERVISOR_ERROR',
  'FLOW_ERROR',
])

function emitSharedState(nextState: Partial<SharedChatStreamState>) {
  sharedState = { ...sharedState, ...nextState }
  sharedListeners.forEach((listener) => listener())
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

function getLatestStreamSessionId(events: SSEEvent[]): string | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const sessionId = getStreamEventSessionId(events[index])
    if (sessionId) {
      return sessionId
    }
  }

  return null
}

function hasStoppedRunEvent(events: SSEEvent[]): boolean {
  return events.some((event) => event.type === 'STOP_CONFIRMED')
}

function hasTerminalErrorRunEvent(events: SSEEvent[]): boolean {
  return events.some((event) => CHAT_RUN_TERMINAL_ERROR_EVENT_TYPES.has(event.type))
}

function buildChatRunActivitySummary(state: SharedChatStreamState): ChatRunActivitySummary {
  const stopped = hasStoppedRunEvent(state.events)
  const errored = Boolean(state.error) || hasTerminalErrorRunEvent(state.events)
  const terminalStatus: ChatRunTerminalStatus = stopped
    ? 'stopped'
    : errored
      ? 'error'
      : state.events.length > 0 && !state.isLoading
        ? 'finished'
        : 'idle'

  return {
    isLoading: state.isLoading,
    error: state.error,
    latestSessionId: getLatestStreamSessionId(state.events),
    terminalStatus,
    eventStreamVersion: state.eventStreamVersion,
  }
}

function areChatRunActivitySummariesEqual(
  current: ChatRunActivitySummary,
  next: ChatRunActivitySummary,
): boolean {
  return current.isLoading === next.isLoading
    && current.error === next.error
    && current.latestSessionId === next.latestSessionId
    && current.terminalStatus === next.terminalStatus
    && current.eventStreamVersion === next.eventStreamVersion
}

/**
 * Hook for managing chat SSE stream
 *
 * @returns Stream state and control functions
 */
export function useChatStream(): UseChatStreamReturn {
  const [snapshot, setSnapshot] = useState<SharedChatStreamState>(sharedState)

  useEffect(() => {
    const listener = () => setSnapshot(sharedState)
    sharedListeners.add(listener)
    return () => {
      sharedListeners.delete(listener)
    }
  }, [])

  const clearEvents = useCallback(() => {
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
    if (sharedAbortController) {
      sharedAbortController.abort()
      sharedAbortController = null
    }
    emitSharedState({ isLoading: false })
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

    if (sharedState.isLoading) {
      console.warn('Cannot start a new chat message while another stream is active')
      return
    }

    sharedAbortController = new AbortController()

    emitSharedState({ isLoading: true, error: null })

    // Start each run with a fresh stream so consumers do not have to reconcile
    // stale events from prior turns before processing the new audit trail.
    replaceSharedEvents([
      {
        type: 'AGENT_GENERATING',
        session_id: sessionId,
        turn_id: options?.turnId,
        timestamp: new Date().toISOString(),
        details: {
          agentRole: 'System',
          agentDisplayName: 'System',
          message: 'Initializing AI agents'
        }
      }
    ])

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message,
          session_id: sessionId,
          turn_id: options?.turnId,
        }),
        signal: sharedAbortController.signal
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('Response body is not readable')
      }

      const decoder = new TextDecoder()
      let buffer = '' // Accumulate partial chunks

      // Read stream chunks
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break

        // Append new chunk to buffer
        buffer += decoder.decode(value, { stream: true })

        // Process complete SSE events (terminated by \n\n)
        const eventBoundary = '\n\n'
        let boundaryIndex: number

        while ((boundaryIndex = buffer.indexOf(eventBoundary)) !== -1) {
          // Extract complete event
          const eventData = buffer.substring(0, boundaryIndex)
          buffer = buffer.substring(boundaryIndex + eventBoundary.length)

          // Parse event lines
          const lines = eventData.split('\n')
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6)
              if (data === '[DONE]') {
                break
              }
              try {
                const parsed: SSEEvent = JSON.parse(data)
                debug.log('🔍 [useChatStream] Received SSE event:', parsed.type, parsed)

                // Add event to events array
                updateSharedEvents(prev => [...prev, parsed])
              } catch (parseError) {
                console.error('Failed to parse SSE event:', parseError, data)
              }
            }
          }
        }
      }

      sharedAbortController = null
      emitSharedState({ isLoading: false })
    } catch (err) {
      // Ignore abort errors (user cancelled)
      if (err instanceof Error && err.name === 'AbortError') {
        debug.log('Stream aborted by user')
        emitSharedState({ isLoading: false })
        return
      }

      const error = err instanceof Error ? err : new Error('Unknown error during streaming')
      emitSharedState({ error, isLoading: false })
      console.error('Error in chat stream:', error)
    } finally {
      sharedAbortController = null
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

    if (sharedState.isLoading) {
      console.warn('Cannot start a new flow execution while another stream is active')
      return
    }

    const turnId = options?.turnId ?? buildClientTurnId()
    sharedAbortController = new AbortController()
    emitSharedState({ isLoading: true, error: null })

    // Start each flow execution with a fresh stream for the same reason as
    // normal chat sends: right-panel consumers should only process this run.
    replaceSharedEvents([
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
      const response = await fetch('/api/chat/execute-flow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          flow_id: flowId,
          session_id: sessionId,
          turn_id: turnId,
          document_id: documentId || null,
          user_query: userQuery || null
        }),
        signal: sharedAbortController.signal
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('Response body is not readable')
      }

      const decoder = new TextDecoder()
      let buffer = ''

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const eventBoundary = '\n\n'
        let boundaryIndex: number

        while ((boundaryIndex = buffer.indexOf(eventBoundary)) !== -1) {
          const eventData = buffer.substring(0, boundaryIndex)
          buffer = buffer.substring(boundaryIndex + eventBoundary.length)

          const lines = eventData.split('\n')
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6)
              if (data === '[DONE]') break
              try {
                const parsed: SSEEvent = JSON.parse(data)
                debug.log('🔍 [useChatStream] Flow SSE event:', parsed.type, parsed)
                updateSharedEvents(prev => [...prev, parsed])
              } catch (parseError) {
                console.error('Failed to parse SSE event:', parseError, data)
              }
            }
          }
        }
      }

      sharedAbortController = null
      emitSharedState({ isLoading: false })
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        debug.log('Flow execution aborted by user')
        emitSharedState({ isLoading: false })
        return
      }
      const error = err instanceof Error ? err : new Error('Unknown error during flow execution')
      emitSharedState({ error, isLoading: false })
      console.error('Error in flow execution:', error)
    } finally {
      sharedAbortController = null
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

export function useChatRunActivitySummary(): ChatRunActivitySummary {
  const [summary, setSummary] = useState<ChatRunActivitySummary>(() => (
    buildChatRunActivitySummary(sharedState)
  ))
  const summaryRef = useRef(summary)

  useEffect(() => {
    const listener = () => {
      const nextSummary = buildChatRunActivitySummary(sharedState)
      if (areChatRunActivitySummariesEqual(summaryRef.current, nextSummary)) {
        return
      }

      summaryRef.current = nextSummary
      setSummary(nextSummary)
    }

    sharedListeners.add(listener)
    return () => {
      sharedListeners.delete(listener)
    }
  }, [])

  return summary
}
