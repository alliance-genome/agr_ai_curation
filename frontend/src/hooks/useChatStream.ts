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
  ownerToken: symbol
  sessionId: string
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
let retainedEventOwnerToken: symbol | null = null
let retainedEventSessionId: string | null = null

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

function getRunTerminalStatus(events: SSEEvent[]): ChatRunTerminalStatus {
  return events.some((event) => event.type.toUpperCase().includes('ERROR'))
    ? 'error'
    : 'completed'
}

function emitChatRunTerminal(detail: ChatRunTerminalEventDetail) {
  window.dispatchEvent(new CustomEvent<ChatRunTerminalEventDetail>(CHAT_RUN_TERMINAL_EVENT, {
    detail,
  }))
}

function startStreamRun(
  ownerToken: symbol,
  sessionId: string,
): ActiveStreamRun | null {
  // Restart policy: reject starts while an owner is active. A user stop releases
  // that owner synchronously, so a replacement need not wait for stale work to settle.
  if (activeStreamRun) {
    return null
  }

  const run = {
    runId: ++nextRunId,
    controller: new AbortController(),
    ownerToken,
    sessionId,
  }
  activeStreamRun = run
  emitSharedState({ isLoading: true, error: null })
  return run
}

function replaceRunEvents(
  run: ActiveStreamRun,
  events: SSEEvent[],
) {
  retainedEventOwnerToken = run.ownerToken
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

function cleanupOwnedStream(ownerToken: symbol, sessionId?: string | null) {
  const run = activeStreamRun
  if (
    run
    && run.ownerToken === ownerToken
    && (sessionId === undefined || run.sessionId === sessionId)
  ) {
    run.controller.abort()
    releaseStreamRun(run)
  }

  if (
    retainedEventOwnerToken === ownerToken
    && (sessionId === undefined || retainedEventSessionId === sessionId)
  ) {
    retainedEventOwnerToken = null
    retainedEventSessionId = null
    replaceSharedEvents([])
    emitSharedState({ error: null })
  }
}

/**
 * Hook for managing chat SSE stream
 *
 * The hook instance owns its stream; unmounting it aborts the run instead of
 * preserving it across remounts.
 *
 * @param activeSessionId Session whose owned stream should be cleaned up when it changes.
 * @returns Stream state and control functions
 */
export function useChatStream(activeSessionId?: string | null): UseChatStreamReturn {
  const [snapshot, setSnapshot] = useState<SharedChatStreamState>(sharedState)
  const ownerToken = useRef(Symbol('chat-stream-owner')).current

  useEffect(() => {
    const listener = () => setSnapshot(sharedState)
    sharedListeners.add(listener)
    return () => {
      sharedListeners.delete(listener)
      cleanupOwnedStream(ownerToken)
    }
  }, [ownerToken])

  useEffect(() => {
    if (!activeSessionId) {
      return
    }

    return () => cleanupOwnedStream(ownerToken, activeSessionId)
  }, [activeSessionId, ownerToken])

  const clearEvents = useCallback(() => {
    retainedEventOwnerToken = null
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

    const run = startStreamRun(ownerToken, sessionId)
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
        signal: run.controller.signal
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
        if (!ownsActiveRun(run)) return

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
                if (ownsActiveRun(run)) {
                  updateSharedEvents(prev => [...prev, parsed])
                }
              } catch (parseError) {
                console.error('Failed to parse SSE event:', parseError, data)
              }
            }
          }
        }
      }

      if (!ownsActiveRun(run)) return

      const terminalStatus = getRunTerminalStatus(sharedState.events)
      const eventStreamVersion = sharedState.eventStreamVersion
      releaseStreamRun(run)
      emitChatRunTerminal({
        sessionId,
        runKind: 'chat',
        status: terminalStatus,
        eventStreamVersion,
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
  }, [ownerToken])

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
    const run = startStreamRun(ownerToken, sessionId)
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
        signal: run.controller.signal
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
        if (!ownsActiveRun(run)) return

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
                if (ownsActiveRun(run)) {
                  updateSharedEvents(prev => [...prev, parsed])
                }
              } catch (parseError) {
                console.error('Failed to parse SSE event:', parseError, data)
              }
            }
          }
        }
      }

      if (!ownsActiveRun(run)) return

      const terminalStatus = getRunTerminalStatus(sharedState.events)
      const eventStreamVersion = sharedState.eventStreamVersion
      releaseStreamRun(run)
      emitChatRunTerminal({
        sessionId,
        runKind: 'flow',
        status: terminalStatus,
        eventStreamVersion,
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
  }, [ownerToken])

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
