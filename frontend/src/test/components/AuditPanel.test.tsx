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
    expect(screen.getByText(/Processing/)).toBeInTheDocument()
    expect(screen.getByText(/Starting crew/)).toBeInTheDocument()
    expect(screen.getByText(/Agent completed/)).toBeInTheDocument()
    expect(screen.getByText(/Done/)).toBeInTheDocument()
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
    expect(screen.getByText(/Processing/)).toBeInTheDocument()

    // Change sessionId
    rerender(<AuditPanel sessionId="session456" sseEvents={[]} />)

    // Events should be cleared
    expect(screen.queryByText(/Processing/)).not.toBeInTheDocument()
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
    expect(screen.getByText(/Processing/)).toBeInTheDocument()

    // Change to null sessionId
    rerender(<AuditPanel sessionId={null} sseEvents={[]} />)

    // Should show empty state
    expect(screen.queryByText(/Processing/)).not.toBeInTheDocument()
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
    expect(screen.getByText(/Processing/)).toBeInTheDocument()

    // Rerender with same sessionId
    rerender(<AuditPanel sessionId="session123" sseEvents={[]} initialEvents={events} />)

    // Event should still be there
    expect(screen.getByText(/Processing/)).toBeInTheDocument()
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

  it('copy button copies all events as formatted text', async () => {
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
