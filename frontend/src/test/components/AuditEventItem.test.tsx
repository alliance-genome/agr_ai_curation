/**
 * Tests for AuditEventItem Component (T017)
 *
 * Tests the individual audit event item component that displays a single event
 * with prefix, label, severity styling, and optional query details.
 */

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AuditEvent } from '../../types/AuditEvent'
import AuditEventItem from '../../components/AuditEventItem'

// Helper function to create test events
function createTestEvent(
  type: AuditEvent['type'],
  details: AuditEvent['details']
): AuditEvent {
  return {
    id: '123e4567-e89b-12d3-a456-426614174000',
    type,
    timestamp: new Date('2025-10-23T10:30:00.000Z'),
    sessionId: 'session123',
    details
  }
}

// ===================================================================
// Basic Rendering Tests
// ===================================================================
describe('AuditEventItem - Basic Rendering (T017)', () => {
  it('renders single event with correct prefix and label', () => {
    const event = createTestEvent('SUPERVISOR_START', {
      message: 'Processing user query'
    })

    render(<AuditEventItem event={event} />)

    // Should display prefix
    expect(screen.getByText(/\[SUPERVISOR\]/)).toBeInTheDocument()

    // Should display label
    expect(screen.getByText(/Processing user query/)).toBeInTheDocument()
  })

  it('renders event with data-testid attribute', () => {
    const event = createTestEvent('CREW_START', {
      crewName: 'disease_ontology'
    })

    const { container } = render(<AuditEventItem event={event} />)

    // Should have data-testid for targeting in parent components
    const eventItem = container.querySelector('[data-testid="audit-event-item"]')
    expect(eventItem).toBeInTheDocument()
  })
})

// ===================================================================
// Severity Styling Tests
// ===================================================================
describe('AuditEventItem - Severity Styling (T017)', () => {
  it('displays info severity styling', () => {
    const event = createTestEvent('SUPERVISOR_START', {
      message: 'Processing user query'
    })

    const { container } = render(<AuditEventItem event={event} />)

    // Should have info severity class or data attribute
    const eventItem = container.querySelector('[data-severity="info"]')
    expect(eventItem).toBeInTheDocument()
  })

  it('displays success severity styling for COMPLETE events', () => {
    const event = createTestEvent('AGENT_COMPLETE', {
      agentRole: 'disease_ontology_agent'
    })

    const { container } = render(<AuditEventItem event={event} />)

    // Should have success severity class or data attribute
    const eventItem = container.querySelector('[data-severity="success"]')
    expect(eventItem).toBeInTheDocument()
  })

  it('displays error severity styling for ERROR events', () => {
    const event = createTestEvent('SUPERVISOR_ERROR', {
      error: 'Database connection failed'
    })

    const { container } = render(<AuditEventItem event={event} />)

    // Should have error severity class or data attribute
    const eventItem = container.querySelector('[data-severity="error"]')
    expect(eventItem).toBeInTheDocument()
  })
})

// ===================================================================
// Query Details Tests (TOOL_START events)
// ===================================================================
describe('AuditEventItem - Query Details (T017)', () => {
  it('shows SQL query details for TOOL_START with query', () => {
    const event = createTestEvent('TOOL_START', {
      toolName: 'sql_query_tool',
      friendlyName: 'Searching database...',
      toolArgs: {
        query: "SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'"
      }
    })

    render(<AuditEventItem event={event} />)

    // Should display the SQL query
    expect(screen.getByText(/Query:/)).toBeInTheDocument()
    expect(screen.getByText(/SELECT \* FROM ontology_terms/)).toBeInTheDocument()
  })

  it('shows API params for TOOL_START with REST API call', () => {
    const event = createTestEvent('TOOL_START', {
      toolName: 'rest_api_call',
      friendlyName: 'Calling external API...',
      toolArgs: {
        url: 'https://api.example.com/genes/ENT1',
        method: 'POST'
      }
    })

    render(<AuditEventItem event={event} />)

    // Should display the API method and URL
    expect(screen.getByText(/POST https:\/\/api\.example\.com\/genes\/ENT1/)).toBeInTheDocument()
  })

  it('does not show query details for TOOL_START without toolArgs', () => {
    const event = createTestEvent('TOOL_START', {
      toolName: 'sql_query_tool',
      friendlyName: 'Searching database...'
    })

    render(<AuditEventItem event={event} />)

    // Should display friendly name
    expect(screen.getByText(/Searching database\.\.\./)).toBeInTheDocument()

    // Should NOT display query label
    expect(screen.queryByText(/Query:/)).not.toBeInTheDocument()
  })
})

// ===================================================================
// All Event Types Tests
// ===================================================================
describe('AuditEventItem - All Event Types (T017)', () => {
  it('handles SUPERVISOR_START event', () => {
    const event = createTestEvent('SUPERVISOR_START', {
      message: 'Processing user query'
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[SUPERVISOR\]/)).toBeInTheDocument()
    expect(screen.getByText(/Processing user query/)).toBeInTheDocument()
  })

  it('handles SUPERVISOR_DISPATCH event', () => {
    const event = createTestEvent('SUPERVISOR_DISPATCH', {
      domainName: 'internal_db_domain',
      stepNumber: 1,
      totalSteps: 2
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[SUPERVISOR\]/)).toBeInTheDocument()
    expect(screen.getByText(/Dispatching domain: Database Search/)).toBeInTheDocument()
    expect(screen.getByText(/step 1\/2/)).toBeInTheDocument()
  })

  it('handles CREW_START event', () => {
    const event = createTestEvent('CREW_START', {
      crewName: 'disease_ontology',
      crewDisplayName: 'Disease Ontology Crew'
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[CREW\]/)).toBeInTheDocument()
    expect(screen.getByText(/Starting crew: Disease Ontology Crew/)).toBeInTheDocument()
  })

  it('handles AGENT_COMPLETE event', () => {
    const event = createTestEvent('AGENT_COMPLETE', {
      agentRole: 'disease_ontology_agent',
      agentDisplayName: 'Disease Ontology Agent',
      crewName: 'disease_ontology'
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[AGENT\]/)).toBeInTheDocument()
    expect(screen.getByText(/Agent completed: Disease Ontology Agent/)).toBeInTheDocument()
  })

  it('handles TOOL_START event', () => {
    const event = createTestEvent('TOOL_START', {
      toolName: 'sql_query_tool',
      friendlyName: 'Searching database...'
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[TOOL\]/)).toBeInTheDocument()
    expect(screen.getByText(/Searching database\.\.\./)).toBeInTheDocument()
  })

  it('handles TOOL_COMPLETE event', () => {
    const event = createTestEvent('TOOL_COMPLETE', {
      toolName: 'sql_query_tool',
      friendlyName: 'Database search complete',
      success: true
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[TOOL\]/)).toBeInTheDocument()
    expect(screen.getByText(/Database search complete/)).toBeInTheDocument()
  })

  it('handles LLM_CALL event', () => {
    const event = createTestEvent('LLM_CALL', {
      message: 'Thinking...',
      agent: 'disease_ontology_agent'
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[LLM\]/)).toBeInTheDocument()
    expect(screen.getByText(/Thinking\.\.\./)).toBeInTheDocument()
  })

  it('handles SUPERVISOR_RESULT event', () => {
    const event = createTestEvent('SUPERVISOR_RESULT', {
      domainName: 'internal_db_domain',
      stepNumber: 1,
      hasError: false
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[SUPERVISOR\]/)).toBeInTheDocument()
    expect(screen.getByText(/Results from Database Search/)).toBeInTheDocument()
  })

  it('handles SUPERVISOR_COMPLETE event', () => {
    const event = createTestEvent('SUPERVISOR_COMPLETE', {
      message: 'Query completed successfully',
      totalSteps: 3
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[SUPERVISOR\]/)).toBeInTheDocument()
    expect(screen.getByText(/Query completed successfully/)).toBeInTheDocument()
    expect(screen.getByText(/3 steps executed/)).toBeInTheDocument()
  })

  it('handles SUPERVISOR_ERROR event', () => {
    const event = createTestEvent('SUPERVISOR_ERROR', {
      error: 'Database connection failed',
      crewName: 'disease_ontology'
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/\[SUPERVISOR ERROR\]/)).toBeInTheDocument()
    expect(screen.getByText(/Database connection failed/)).toBeInTheDocument()
  })
})

// ===================================================================
// Edge Cases
// ===================================================================
describe('AuditEventItem - Edge Cases (T017)', () => {
  it('handles events with missing optional fields gracefully', () => {
    const event = createTestEvent('AGENT_COMPLETE', {
      agentRole: 'test_agent'
      // Missing agentDisplayName and crewName
    })

    render(<AuditEventItem event={event} />)

    // Should still render without crashing
    expect(screen.getByText(/\[AGENT\]/)).toBeInTheDocument()
    expect(screen.getByText(/Agent completed: test_agent/)).toBeInTheDocument()
  })

  it('handles TOOL_COMPLETE with failure status', () => {
    const event = createTestEvent('TOOL_COMPLETE', {
      toolName: 'rest_api_call',
      friendlyName: 'API call complete',
      success: false
    })

    render(<AuditEventItem event={event} />)

    expect(screen.getByText(/API call complete/)).toBeInTheDocument()
    expect(screen.getByText(/\(failed\)/)).toBeInTheDocument()
  })

  it('handles very long SQL queries without breaking layout', () => {
    const longQuery = 'SELECT * FROM ontology_terms WHERE term_id IN (' +
      "'DOID:1', 'DOID:2', 'DOID:3', 'DOID:4', 'DOID:5', ".repeat(20) +
      "'DOID:999')"

    const event = createTestEvent('TOOL_START', {
      toolName: 'sql_query_tool',
      friendlyName: 'Searching database...',
      toolArgs: {
        query: longQuery
      }
    })

    const { container } = render(<AuditEventItem event={event} />)

    // Should render without error
    expect(container).toBeInTheDocument()

    // Should display query (even if truncated by CSS)
    expect(screen.getByText(/Query:/)).toBeInTheDocument()
  })
})
