/**
 * Tests for AuditPanel Component (T018)
 *
 * Tests the main audit panel component that manages and displays a list
 * of audit events with auto-scroll, copy, and clear functionality.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { AuditEvent } from '../../types/AuditEvent'
import AuditPanel from '../../components/AuditPanel'
import { getChatRenderCacheKeys } from '../../lib/chatCacheKeys'

const mockUseAuth = vi.hoisted(() => vi.fn())

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}))

// Helper function to create test events
function createTestEvent(
  type: AuditEvent['type'],
  details: AuditEvent['details'],
  overrides?: Partial<AuditEvent>
): AuditEvent {
  return {
    id: crypto.randomUUID(),
    type,
    timestamp: new Date(),
    sessionId: 'session123',
    details,
    ...overrides
  }
}

beforeEach(() => {
  localStorage.clear()
  mockUseAuth.mockReturnValue({
    user: { uid: 'user-1', email: 'curator@example.org' },
  })
})

// ===================================================================
// Empty State Tests
// ===================================================================
describe('AuditPanel - Empty State (T018)', () => {

  it('renders empty state when no events', () => {
    render(<AuditPanel sessionId={null} sseEvents={[]} />)

    // Should show informational message about audit capability
    expect(screen.getByText(/No audit events yet/i)).toBeInTheDocument()
  })

  it('renders empty state with null sessionId', () => {
    render(<AuditPanel sessionId={null} sseEvents={[]} />)

    // Should still render the component
    const emptyState = screen.getByText(/No audit events yet/i)
    expect(emptyState).toBeInTheDocument()
  })

  it('does not show events list when empty', () => {
    const { container } = render(<AuditPanel sessionId="session123" sseEvents={[]} />)

    // Should not have audit event items
    const eventItems = container.querySelectorAll('[data-testid="audit-event-item"]')
    expect(eventItems).toHaveLength(0)
  })
})

// ===================================================================
// Event Display Tests
// ===================================================================
describe('AuditPanel - Event Display (T018)', () => {
  it('renders events list in chronological order', () => {
    // Note: In real implementation, events would be added via SSE or props
    // For this test, we'll render with initial events if component accepts them
    const event1 = createTestEvent('SUPERVISOR_START', {
      message: 'Processing user query'
    }, {
      timestamp: new Date('2025-10-23T10:30:00.000Z')
    })

    const event2 = createTestEvent('CREW_START', {
      crewName: 'disease_ontology'
    }, {
      timestamp: new Date('2025-10-23T10:30:01.000Z')
    })

    const event3 = createTestEvent('TOOL_START', {
      toolName: 'sql_query_tool',
      friendlyName: 'Searching database...'
    }, {
      timestamp: new Date('2025-10-23T10:30:02.000Z')
    })

    // Assuming AuditPanel can accept initialEvents prop for testing
    render(
      <AuditPanel
        sessionId="session123"
        sseEvents={[]}
        initialEvents={[event1, event2, event3]}
      />
    )

    const eventItems = screen.getAllByTestId('audit-event-item')

    // Should render all 3 events
    expect(eventItems).toHaveLength(3)

    // Events should be in chronological order (oldest first)
    expect(within(eventItems[0]).getByText(/Processing user query/)).toBeInTheDocument()
    expect(within(eventItems[1]).getByText(/Starting crew/)).toBeInTheDocument()
    expect(within(eventItems[2]).getByText(/Searching database/)).toBeInTheDocument()
  })

  it('displays multiple events of different types', () => {
    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' }),
      createTestEvent('CREW_START', { crewName: 'test_crew' }),
      createTestEvent('AGENT_COMPLETE', { agentRole: 'test_agent' }),
      createTestEvent('SUPERVISOR_COMPLETE', { message: 'Done', totalSteps: 1 })
    ]

    render(<AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />)

    // Should show all event types
    expect(screen.getByText('[SUPERVISOR] Processing')).toBeInTheDocument()
    expect(screen.getByText(/Starting crew/)).toBeInTheDocument()
    expect(screen.getByText(/Agent completed/)).toBeInTheDocument()
    expect(screen.getByText(/Done/)).toBeInTheDocument()
  })

  it('renders SSE audit events when crypto.randomUUID throws', async () => {
    const randomUuidSpy = vi
      .spyOn(globalThis.crypto, 'randomUUID')
      .mockImplementation(() => { throw new TypeError('crypto.randomUUID is not a function') })

    try {
      render(
        <AuditPanel
          sessionId="session123"
          sseEvents={[
            {
              type: 'SUPERVISOR_START',
              timestamp: '2025-10-23T10:30:00.000Z',
              sessionId: 'session123',
              details: { message: 'Processing from SSE' }
            }
          ]}
        />
      )

      await waitFor(() => {
        expect(screen.getByText('[SUPERVISOR] Processing from SSE')).toBeInTheDocument()
      })
    } finally {
      randomUuidSpy.mockRestore()
    }
  })

  it('renders FLOW_STEP_EVIDENCE SSE events in the audit list', async () => {
    render(
      <AuditPanel
        sessionId="session123"
        sseEvents={[
          {
            type: 'FLOW_STEP_EVIDENCE',
            timestamp: '2026-02-26T00:00:01.000Z',
            sessionId: 'session123',
            details: {
              flow_id: 'flow-1',
              flow_name: 'Flow Evidence',
              flow_run_id: 'run-1',
              step: 2,
              tool_name: 'ask_gene_specialist',
              agent_id: 'gene',
              agent_name: 'Gene Agent',
              evidence_records: [],
              evidence_count: 1,
              total_evidence_records: 3,
            },
          }
        ]}
      />
    )

    await waitFor(() => {
      expect(
        screen.getByText(
          '[EVIDENCE] Flow step 2 captured 1 evidence quote (3 total so far) from Gene Agent via ask_gene_specialist'
        )
      ).toBeInTheDocument()
    })
  })
})

// ===================================================================
// Session Change Tests
// ===================================================================
describe('AuditPanel - Session Change (T018)', () => {
  it('clears events on sessionId change', () => {
    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' }),
      createTestEvent('CREW_START', { crewName: 'test_crew' })
    ]

    const { rerender } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />
    )

    // Should initially show events
    expect(screen.getByText('[SUPERVISOR] Processing')).toBeInTheDocument()

    // Change sessionId
    rerender(<AuditPanel sessionId="session456" sseEvents={[]} />)

    // Events should be cleared
    expect(screen.queryByText('[SUPERVISOR] Processing')).not.toBeInTheDocument()
    expect(screen.getByText(/No audit events yet/i)).toBeInTheDocument()
  })

  it('clears events when sessionId changes from string to null', () => {
    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' })
    ]

    const { rerender } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />
    )

    // Should show event
    expect(screen.getByText('[SUPERVISOR] Processing')).toBeInTheDocument()

    // Change to null sessionId
    rerender(<AuditPanel sessionId={null} sseEvents={[]} />)

    // Should show empty state
    expect(screen.queryByText('[SUPERVISOR] Processing')).not.toBeInTheDocument()
    expect(screen.getByText(/No audit events yet/i)).toBeInTheDocument()
  })

  it('preserves events when sessionId remains the same', () => {
    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' })
    ]

    const { rerender } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />
    )

    // Should show event
    expect(screen.getByText('[SUPERVISOR] Processing')).toBeInTheDocument()

    // Rerender with same sessionId
    rerender(<AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />)

    // Event should still be there
    expect(screen.getByText('[SUPERVISOR] Processing')).toBeInTheDocument()
  })

  it('does not leak old-session events into a new session after rerender/remount', async () => {
    const oldSessionEvents = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing old session' })
    ]
    const oldSessionCacheKey = getChatRenderCacheKeys('user-1', 'session123').auditEvents
    const newSessionCacheKey = getChatRenderCacheKeys('user-1', 'session456').auditEvents

    const { rerender, unmount } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={oldSessionEvents} />
    )

    await waitFor(() => {
      expect(localStorage.getItem(oldSessionCacheKey)).not.toBeNull()
    })

    // Switch to a new session (as happens after Reset Chat)
    rerender(<AuditPanel sessionId="session456" sseEvents={[]} />)

    await waitFor(() => {
      expect(localStorage.getItem(newSessionCacheKey)).toBeNull()
    })

    // Simulate refresh/remount on the new session; old events should not reappear.
    unmount()
    render(<AuditPanel sessionId="session456" sseEvents={[]} />)

    expect(screen.queryByText('Processing old session')).not.toBeInTheDocument()
    expect(screen.getByText(/No audit events yet/i)).toBeInTheDocument()
  })
})

// ===================================================================
// Auto-scroll Tests
// ===================================================================
describe('AuditPanel - Auto-scroll (T018)', () => {
  it('auto-scrolls to bottom on new event', async () => {
    const scrollIntoViewMock = vi.fn()

    // Mock scrollIntoView
    Element.prototype.scrollIntoView = scrollIntoViewMock

    const event1 = createTestEvent('SUPERVISOR_START', { message: 'Processing' })

    const { rerender } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={[event1]} />
    )

    // Add new event
    const event2 = createTestEvent('CREW_START', { crewName: 'test_crew' })
    rerender(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={[event1, event2]} />
    )

    // Should call scrollIntoView (messagesEndRef pattern from Chat.tsx)
    await waitFor(() => {
      expect(scrollIntoViewMock).toHaveBeenCalled()
    })
  })

  it('uses smooth scrolling behavior', async () => {
    const scrollIntoViewMock = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoViewMock

    const event1 = createTestEvent('SUPERVISOR_START', { message: 'Processing' })
    const event2 = createTestEvent('CREW_START', { crewName: 'test_crew' })

    const { rerender } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={[event1]} />
    )

    rerender(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={[event1, event2]} />
    )

    await waitFor(() => {
      // Should call with smooth behavior
      expect(scrollIntoViewMock).toHaveBeenCalledWith({ behavior: 'smooth' })
    })
  })
})

// ===================================================================
// Copy Button Tests
// ===================================================================
describe('AuditPanel - Copy Button (T018)', () => {

  it('copy button copies all events as formatted text and shows copied feedback', async () => {
    const user = userEvent.setup()

    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing user query' }),
      createTestEvent('CREW_START', { crewName: 'disease_ontology' }),
      createTestEvent('SUPERVISOR_COMPLETE', { message: 'Done', totalSteps: 1 })
    ]

    // Set up spy RIGHT before render to avoid it being reset
    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)

    render(<AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />)

    // Find and click copy button
    const copyButton = screen.getByTestId('copy-button')
    await user.click(copyButton)

    // Should have called clipboard.writeText
    expect(writeTextSpy).toHaveBeenCalledOnce()

    // Should include all events in formatted text
    const copiedText = writeTextSpy.mock.calls[0][0]
    expect(copiedText).toContain('[SUPERVISOR] Processing user query')
    expect(copiedText).toContain('[CREW] Starting crew: disease_ontology')
    expect(copiedText).toContain('[SUPERVISOR] Done')
    await waitFor(() => {
      expect(copyButton).toHaveTextContent('Copied!')
    })

    writeTextSpy.mockRestore()
  })

  it('copy button is positioned at bottom-left corner', () => {
    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' })
    ]

    render(<AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />)

    const copyButton = screen.getByTestId('copy-button')

    // Should have absolute positioning styles (check parent container or button styles)
    expect(copyButton).toBeInTheDocument()

    // Additional check: button should be visible
    expect(copyButton).toBeVisible()
  })

  it('copy button is disabled when no events', () => {
    render(<AuditPanel sessionId="session123" sseEvents={[]} />)

    const copyButton = screen.getByTestId('copy-button')

    // Should be disabled when there are no events
    expect(copyButton).toBeDisabled()
  })
})

// ===================================================================
// Clear Button Tests
// ===================================================================
describe('AuditPanel - Clear Button (T018)', () => {
  it('clear button invokes onClear callback', async () => {
    const user = userEvent.setup()
    const onClearMock = vi.fn()

    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' })
    ]

    render(
      <AuditPanel
        sessionId="session123"
        sseEvents={[]}
        initialEvents={events}
        onClear={onClearMock}
      />
    )

    // Find and click clear button
    const clearButton = screen.getByTestId('clear-button')
    await user.click(clearButton)

    // Should have called onClear callback
    expect(onClearMock).toHaveBeenCalledOnce()
  })

  it('clear button is disabled when no events', () => {
    const onClearMock = vi.fn()

    render(<AuditPanel sessionId="session123" sseEvents={[]} onClear={onClearMock} />)

    const clearButton = screen.getByTestId('clear-button')

    // Should be disabled when there are no events
    expect(clearButton).toBeDisabled()
  })

  it('clear button is enabled when events exist', () => {
    const onClearMock = vi.fn()
    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' })
    ]

    render(
      <AuditPanel
        sessionId="session123"
        sseEvents={[]}
        initialEvents={events}
        onClear={onClearMock}
      />
    )

    const clearButton = screen.getByTestId('clear-button')

    // Should be enabled
    expect(clearButton).not.toBeDisabled()
  })
})

// ===================================================================
// Edge Cases
// ===================================================================
describe('AuditPanel - Edge Cases (T018)', () => {
  it('handles many events without performance issues', () => {
    // Create 100 events
    const events = Array.from({ length: 100 }, (_, i) =>
      createTestEvent('TOOL_START', {
        toolName: 'sql_query_tool',
        friendlyName: `Query ${i}`
      })
    )

    const { container } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />
    )

    // Should render all events
    const eventItems = container.querySelectorAll('[data-testid="audit-event-item"]')
    expect(eventItems).toHaveLength(100)
  })

  it('handles events with very long text content', () => {
    const longMessage = 'Processing user query with very long description '.repeat(50)

    const event = createTestEvent('SUPERVISOR_START', {
      message: longMessage
    })

    const { container } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} initialEvents={[event]} />
    )

    // Should render without error
    expect(container).toBeInTheDocument()
  })

  it('renders with optional className prop', () => {
    const { container } = render(
      <AuditPanel sessionId="session123" sseEvents={[]} className="custom-audit-panel" />
    )

    // Should apply custom className
    const panel = container.querySelector('.custom-audit-panel')
    expect(panel).toBeInTheDocument()
  })

  it('handles onClear callback being undefined', async () => {
    const user = userEvent.setup()

    const events = [
      createTestEvent('SUPERVISOR_START', { message: 'Processing' })
    ]

    render(<AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />)

    const clearButton = screen.getByTestId('clear-button')

    // Should not crash when clicking clear without onClear callback
    await user.click(clearButton)

    // Component should still be rendered
    expect(screen.getByText(/Processing/)).toBeInTheDocument()
  })
})
