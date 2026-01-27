/**
 * AuditPanel Component
 *
 * Top-level audit panel component that displays real-time agent activity.
 * Manages event list, auto-scroll, copy, and clear functionality.
 */

import React, { useState, useEffect, useRef } from 'react'
import { Box, Button, Typography } from '@mui/material'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import ClearIcon from '@mui/icons-material/Clear'
import type { AuditEvent, AuditEventType } from '../types/AuditEvent'
import AuditEventItem from './AuditEventItem'
import { formatAuditEvent, parseSSEEvent } from '../utils/auditHelpers'
import type { SSEEvent } from '../hooks/useChatStream'

/**
 * Props for the AuditPanel component
 */
export interface AuditPanelProps {
  /**
   * Current chat session ID.
   * Used to scope audit events and automatically clear the panel when the session changes.
   */
  sessionId: string | null

  /**
   * Shared SSE events from useChatStream hook (lifted to HomePage).
   * This is the live event stream that powers the audit panel. The panel filters
   * these events to show only audit-relevant event types (all 10 audit event types).
   * Tests should explicitly pass empty array [] to test with no events.
   */
  sseEvents: SSEEvent[]

  /**
   * Optional initial events for testing purposes.
   * Allows tests to pre-populate the panel with events without going through SSE.
   */
  initialEvents?: AuditEvent[]

  /**
   * Callback when user clicks the clear button.
   * Parent component can use this to perform additional cleanup if needed.
   */
  onClear?: () => void

  /**
   * Optional className for custom styling of the root container.
   */
  className?: string

  /**
   * Optional stop handler to abort current run.
   */
  onStop?: () => void

  /**
   * Whether a run is currently streaming (disables stop when false).
   */
  isStreaming?: boolean
}

/**
 * Main audit panel component that displays real-time AI agent activity.
 *
 * The AuditPanel component provides curators with a comprehensive view of all agent
 * actions, including supervisor decisions, crew dispatches, database queries, API
 * calls, and LLM reasoning. It subscribes to a unified SSE event stream and displays
 * all 10 audit event types in chronological order.
 *
 * Key features:
 * - **Real-time updates**: Automatically processes incoming SSE events
 * - **Session scoping**: Events are filtered by session ID and cleared on session change
 * - **Auto-scroll**: Smoothly scrolls to bottom when new events arrive
 * - **Copy to clipboard**: Allows curators to copy all events as formatted text
 * - **Manual clear**: Provides a clear button to reset the panel
 * - **Query transparency**: Displays SQL queries and API parameters inline
 *
 * The panel uses the messagesEndRef pattern from Chat.tsx for scroll behavior and
 * matches the chat panel's styling with custom scrollbar, button placement, and
 * responsive layout.
 *
 * @component
 * @example
 * ```tsx
 * import { useState } from 'react'
 * import { useChatStream } from '../hooks/useChatStream'
 *
 * function HomePage() {
 *   // Session ID managed separately
 *   const [sessionId, setSessionId] = useState<string | null>(null)
 *
 *   // Shared SSE stream (events renamed to sseEvents when passing to AuditPanel)
 *   const { events, isLoading, sendMessage } = useChatStream()
 *
 *   return (
 *     <AuditPanel
 *       sessionId={sessionId}
 *       sseEvents={events}
 *       onClear={() => console.log('Panel cleared')}
 *     />
 *   )
 * }
 * ```
 */
// Helper to save audit events to localStorage
const saveAuditEventsToStorage = (sessionId: string, events: AuditEvent[]) => {
  try {
    localStorage.setItem(`audit_events_${sessionId}`, JSON.stringify(events))
  } catch (e) {
    console.error('Failed to save audit events to localStorage:', e)
  }
}

// Helper to load audit events from localStorage
const loadAuditEventsFromStorage = (sessionId: string): AuditEvent[] => {
  try {
    const stored = localStorage.getItem(`audit_events_${sessionId}`)
    if (stored) {
      const events = JSON.parse(stored)
      // Restore Date objects
      return events.map((e: any) => ({
        ...e,
        timestamp: new Date(e.timestamp),
      }))
    }
  } catch (e) {
    console.error('Failed to load audit events from localStorage:', e)
  }
  return []
}

// Helper to clear audit events from localStorage
const clearAuditEventsFromStorage = (sessionId: string) => {
  try {
    localStorage.removeItem(`audit_events_${sessionId}`)
  } catch (e) {
    console.error('Failed to clear audit events from localStorage:', e)
  }
}

const AuditPanel: React.FC<AuditPanelProps> = ({
  sessionId,
  sseEvents,
  initialEvents = [],
  onClear,
  className,
  onStop,
  isStreaming = false
}) => {
  // Initialize events from initialEvents prop or localStorage
  const [events, setEvents] = useState<AuditEvent[]>(() => {
    if (initialEvents.length > 0) {
      return initialEvents
    }
    if (sessionId) {
      return loadAuditEventsFromStorage(sessionId)
    }
    return []
  })
  const [prevSessionId, setPrevSessionId] = useState<string | null>(sessionId)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const processedEventIndicesRef = useRef<Set<number>>(new Set())

  /**
   * Filter function to identify audit event types
   * NOTE: AGENT_THINKING is excluded because it emits per-token events that
   * flood the panel with word-by-word reasoning. AGENT_GENERATING is kept
   * as it's emitted once per phase.
   */
  const isAuditEvent = (eventType: string): eventType is AuditEventType => {
    const auditEventTypes: AuditEventType[] = [
      'SUPERVISOR_START',
      'SUPERVISOR_DISPATCH',
      'CREW_START',
      'AGENT_COMPLETE',
      'AGENT_GENERATING',
      // 'AGENT_THINKING' - excluded: per-token events create noise (word-by-word reasoning)
      'TOOL_START',
      'TOOL_COMPLETE',
      'LLM_CALL',
      'SUPERVISOR_RESULT',
      'SUPERVISOR_COMPLETE',
      'SUPERVISOR_ERROR',
      'SPECIALIST_RETRY',
      'SPECIALIST_RETRY_SUCCESS',
      'SPECIALIST_ERROR',
      'FORMATTER_PROCESSING',
      'DOMAIN_PLAN_CREATED',
      'DOMAIN_PLANNING',
      'DOMAIN_EXECUTION_START',
      'DOMAIN_COMPLETED',
      'DOMAIN_CATEGORY_ERROR',
      'DOMAIN_SKIPPED',
      'FILE_READY',
    ]
    return auditEventTypes.includes(eventType as AuditEventType)
  }

  // Load events from localStorage when sessionId changes
  useEffect(() => {
    if (sessionId !== prevSessionId) {
      // Try to load saved events for the new session
      if (sessionId) {
        const savedEvents = loadAuditEventsFromStorage(sessionId)
        setEvents(savedEvents)
      } else {
        setEvents([])
      }
      setPrevSessionId(sessionId)
      // Reset processed events tracker for new session
      processedEventIndicesRef.current = new Set()
    }
  }, [sessionId, prevSessionId])

  // Save events to localStorage whenever they change
  useEffect(() => {
    if (sessionId && events.length > 0) {
      saveAuditEventsToStorage(sessionId, events)
    }
  }, [events, sessionId])

  // Update events when initialEvents prop changes (for testing)
  useEffect(() => {
    if (initialEvents.length > 0 && sessionId === prevSessionId) {
      setEvents(initialEvents)
    }
  }, [initialEvents, sessionId, prevSessionId])

  // T027: Process SSE events and add audit events to state
  useEffect(() => {
    // Process only new events
    const newEvents = sseEvents.slice(processedEventIndicesRef.current.size)

    newEvents.forEach((sseEvent: SSEEvent) => {
      // Filter for audit event types only
      if (!isAuditEvent(sseEvent.type)) {
        return
      }

      // Parse SSE event to AuditEvent
      try {
        // Ensure event has required fields for parseSSEEvent
        if (!sseEvent.timestamp || !sseEvent.sessionId) {
          console.warn('🔍 [AUDIT] Skipping event missing required fields:', sseEvent)
          return
        }

        const auditEvent = parseSSEEvent({
          type: sseEvent.type as AuditEventType,
          timestamp: sseEvent.timestamp,
          sessionId: sseEvent.sessionId,
          details: sseEvent.details || {}
        })

        // Only add events matching current session
        if (auditEvent.sessionId === sessionId) {
          setEvents(prev => [...prev, auditEvent])
        }
      } catch (err) {
        console.error('🔍 [AUDIT] Failed to parse audit event:', err, sseEvent)
      }
    })

    // Mark all events as processed
    processedEventIndicesRef.current = new Set(Array.from({ length: sseEvents.length }, (_, i) => i))
  }, [sseEvents, sessionId, isAuditEvent])

  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    if (messagesEndRef.current && events.length > 0) {
      // Check if scrollIntoView is available (may not be in test environment)
      if (typeof messagesEndRef.current.scrollIntoView === 'function') {
        messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
      }
    }
  }, [events])

  // Handle copy button click
  const handleCopy = () => {
    const formattedText = events.map(event => formatAuditEvent(event)).join('\n')

    // Check if Clipboard API is available
    if (!navigator?.clipboard?.writeText) {
      console.error('Clipboard API not available. Copy functionality requires HTTPS or localhost.')
      return
    }

    // Call clipboard API (will be mocked in tests)
    navigator.clipboard.writeText(formattedText).catch(err => {
      console.error('Failed to copy audit events:', err)
    })
  }

  // Handle clear button click
  const handleClear = () => {
    // Clear events from state
    setEvents([])

    // Clear from localStorage
    if (sessionId) {
      clearAuditEventsFromStorage(sessionId)
    }

    // Mark all current SSE events as processed to prevent them from being re-added
    // This preserves the "most recent index processed" marker so the effect sees
    // no new items after a clear and leaves the panel empty until fresh events arrive
    processedEventIndicesRef.current = new Set(Array.from({ length: sseEvents.length }, (_, i) => i))

    // Call optional callback if provided
    if (onClear) {
      onClear()
    }
  }

  const hasEvents = events.length > 0
  const legendItems = [
    { label: 'In Progress', color: 'rgba(33, 150, 243, 0.5)' },
    { label: 'Processing', color: 'rgba(156, 39, 176, 0.5)' },
    { label: 'Success', color: 'rgba(76, 175, 80, 0.5)' },
    { label: 'Error', color: 'rgba(244, 67, 54, 0.5)' }
  ]

  return (
    <Box
      data-testid="audit-panel"
      data-session-id={sessionId || 'null'}
      className={className}
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        position: 'relative',
        backgroundColor: 'transparent',
        borderRadius: '4px'
      }}
    >
      {/* Events container with scroll */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: '1.5rem',
          paddingBottom: '60px', // Space for buttons
          scrollBehavior: 'smooth',
          borderTop: '1px solid rgba(255, 255, 255, 0.08)',
          borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
          // Custom scrollbar styling to match chat panel
          '&::-webkit-scrollbar': {
            width: '8px'
          },
          '&::-webkit-scrollbar-track': {
            background: 'rgba(255, 255, 255, 0.05)',
            borderRadius: '4px'
          },
          '&::-webkit-scrollbar-thumb': {
            background: 'rgba(255, 255, 255, 0.15)',
            borderRadius: '4px'
          },
          '&::-webkit-scrollbar-thumb:hover': {
            background: 'rgba(255, 255, 255, 0.25)'
          }
        }}
      >
        {!hasEvents ? (
          <Typography
            variant="body2"
            sx={{
              textAlign: 'center',
              color: 'rgba(255, 255, 255, 0.5)',
              fontStyle: 'italic',
              padding: '2rem',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%'
            }}
          >
            No audit events yet. Start a conversation to see AI agent activity in real-time.
          </Typography>
        ) : (
          <>
            {events.map((event, index) => (
              <AuditEventItem
                key={event.id}
                event={event}
                isLatest={index === events.length - 1}
              />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </Box>

      {/* Button container at bottom-left */}
      <Box
        sx={{
          position: 'absolute',
          bottom: '12px',
          left: '12px',
          right: '12px',
          display: 'flex',
          gap: '8px',
          alignItems: 'center',
          flexWrap: 'wrap',
          zIndex: 10
        }}
      >
        {/* Copy button */}
        <Button
          data-testid="copy-button"
          variant="outlined"
          size="small"
          startIcon={<ContentCopyIcon fontSize="small" />}
          onClick={handleCopy}
          disabled={!hasEvents}
          sx={{
            minWidth: 'auto',
            textTransform: 'none',
            fontSize: '0.75rem',
            borderColor: 'rgba(255, 255, 255, 0.23)',
            color: 'rgba(255, 255, 255, 0.7)',
            padding: '4px 12px',
            '&:hover': {
              borderColor: 'rgba(255, 255, 255, 0.4)',
              backgroundColor: 'rgba(255, 255, 255, 0.08)'
            },
            '&:disabled': {
              borderColor: 'rgba(255, 255, 255, 0.12)',
              color: 'rgba(255, 255, 255, 0.3)'
            }
          }}
        >
          Copy
        </Button>

        {/* Clear button */}
        <Button
          data-testid="clear-button"
          variant="outlined"
          size="small"
          startIcon={<ClearIcon fontSize="small" />}
          onClick={handleClear}
          disabled={!hasEvents}
          sx={{
            minWidth: 'auto',
            textTransform: 'none',
            fontSize: '0.75rem',
            borderColor: 'rgba(255, 255, 255, 0.23)',
            color: 'rgba(255, 255, 255, 0.7)',
            padding: '4px 12px',
            '&:hover': {
              borderColor: 'rgba(255, 255, 255, 0.4)',
              backgroundColor: 'rgba(255, 255, 255, 0.08)'
            },
            '&:disabled': {
              borderColor: 'rgba(255, 255, 255, 0.12)',
              color: 'rgba(255, 255, 255, 0.3)'
            }
          }}
        >
          Clear
        </Button>

        {/* Stop button (aligned with Copy/Clear) */}
        <Button
          data-testid="stop-button"
          variant="outlined"
          size="small"
          onClick={() => {
            if (onStop) {
              onStop()
            }
          }}
          disabled={!isStreaming || !onStop}
          sx={{
            minWidth: 'auto',
            textTransform: 'none',
            fontSize: '0.75rem',
            borderColor: 'rgba(220, 53, 69, 0.4)',
            color: 'rgba(220, 53, 69, 0.85)',
            backgroundColor: 'rgba(220, 53, 69, 0.08)',
            padding: '4px 12px',
            '&:hover': {
              borderColor: 'rgba(220, 53, 69, 0.6)',
              backgroundColor: 'rgba(220, 53, 69, 0.12)'
            },
            '&:disabled': {
              borderColor: 'rgba(220, 53, 69, 0.12)',
              color: 'rgba(220, 53, 69, 0.3)'
            }
          }}
        >
          Stop
        </Button>

        {/* Legend (aligned with controls) */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, marginLeft: '8px', flexWrap: 'nowrap' }}>
          {legendItems.map(item => (
            <Box key={item.label} sx={{ display: 'flex', alignItems: 'center', gap: 0.4 }}>
              <Box
                sx={{
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  backgroundColor: item.color,
                  border: '1px solid rgba(255, 255, 255, 0.3)',
                  flexShrink: 0
                }}
              />
              <span style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{item.label}</span>
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  )
}

export default AuditPanel
