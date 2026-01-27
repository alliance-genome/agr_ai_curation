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

import { useState, useRef, useCallback } from 'react'
import { debug } from '@/utils/env'

export interface SSEEvent {
  type: string
  [key: string]: any
}

export interface UseChatStreamReturn {
  /**
   * All SSE events received in this session
   */
  events: SSEEvent[]

  /**
   * Whether a stream request is currently in progress
   */
  isLoading: boolean

  /**
   * Send a message and start receiving SSE events
   */
  sendMessage: (message: string, sessionId: string) => Promise<void>

  /**
   * Execute a curation flow with streaming response
   */
  executeFlow: (
    flowId: string,
    sessionId: string,
    documentId?: string,
    userQuery?: string
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
   * Abort the current stream (if any)
   */
  stopStream: (sessionId: string) => Promise<void>
}

/**
 * Hook for managing chat SSE stream
 *
 * @returns Stream state and control functions
 */
export function useChatStream(): UseChatStreamReturn {
  const [events, setEvents] = useState<SSEEvent[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  const clearEvents = useCallback(() => {
    setEvents([])
    setError(null)
  }, [])

  const stopStream = useCallback(async (sessionId: string) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setIsLoading(false)
    // Emit a synthetic event so Audit/Chat can show a stop notice even without SSE
    setEvents(prev => [
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

  const sendMessage = useCallback(async (message: string, sessionId: string) => {
    if (!message.trim()) {
      console.warn('Cannot send empty message')
      return
    }

    if (!sessionId) {
      const err = new Error('No session ID available')
      setError(err)
      console.error(err)
      return
    }

    // Abort any existing stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    // Create new abort controller
    abortControllerRef.current = new AbortController()

    setIsLoading(true)
    setError(null)

    // Emit immediate "initializing" event so user sees feedback right away
    setEvents(prev => [
      ...prev,
      {
        type: 'AGENT_GENERATING',
        sessionId: sessionId,
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
          session_id: sessionId
        }),
        signal: abortControllerRef.current.signal
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
      while (true) {
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
                setEvents(prev => [...prev, parsed])
              } catch (parseError) {
                console.error('Failed to parse SSE event:', parseError, data)
              }
            }
          }
        }
      }

      setIsLoading(false)
    } catch (err) {
      // Ignore abort errors (user cancelled)
      if (err instanceof Error && err.name === 'AbortError') {
        debug.log('Stream aborted by user')
        setIsLoading(false)
        return
      }

      const error = err instanceof Error ? err : new Error('Unknown error during streaming')
      setError(error)
      setIsLoading(false)
      console.error('Error in chat stream:', error)
    }
  }, [])

  /**
   * Execute a curation flow with SSE streaming
   */
  const executeFlow = useCallback(async (
    flowId: string,
    sessionId: string,
    documentId?: string,
    userQuery?: string
  ) => {
    if (!sessionId) {
      const err = new Error('No session ID available')
      setError(err)
      console.error(err)
      return
    }

    // Abort any existing stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    abortControllerRef.current = new AbortController()
    setIsLoading(true)
    setError(null)

    // Emit initializing event
    setEvents(prev => [
      ...prev,
      {
        type: 'AGENT_GENERATING',
        sessionId: sessionId,
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
          document_id: documentId || null,
          user_query: userQuery || null
        }),
        signal: abortControllerRef.current.signal
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

      while (true) {
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
                setEvents(prev => [...prev, parsed])
              } catch (parseError) {
                console.error('Failed to parse SSE event:', parseError, data)
              }
            }
          }
        }
      }

      setIsLoading(false)
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        debug.log('Flow execution aborted by user')
        setIsLoading(false)
        return
      }
      const error = err instanceof Error ? err : new Error('Unknown error during flow execution')
      setError(error)
      setIsLoading(false)
      console.error('Error in flow execution:', error)
    }
  }, [])

  return {
    events,
    isLoading,
    sendMessage,
    executeFlow,
    error,
    clearEvents,
    stopStream
  }
}
