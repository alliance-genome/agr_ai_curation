/**
 * Tests for Audit Helper Functions (T012-T016)
 *
 * These tests follow TDD principles - written before implementation to verify
 * all helper functions work correctly with the 10 audit event types.
 */

import { describe, it, expect } from 'vitest'
import {
  parseSSEEvent,
  formatAuditEvent,
  getEventPrefix,
  getEventLabel,
  getEventSeverity,
} from '../../utils/auditHelpers'
import type { AuditSeverity } from '../../utils/auditHelpers'
import type {
  AuditEvent,
  AuditEventSSE,
  AuditEventType,
  SupervisorStartDetails,
  SupervisorDispatchDetails,
  CrewStartDetails,
  AgentCompleteDetails,
  ToolStartDetails,
  ToolCompleteDetails,
  LLMCallDetails,
  SupervisorResultDetails,
  SupervisorCompleteDetails,
  SupervisorErrorDetails,
} from '../../types/AuditEvent'

// ===================================================================
// T012: Test parseSSEEvent
// ===================================================================
describe('parseSSEEvent (T012)', () => {
  it('converts ISO timestamp to Date object', () => {
    const sseData: AuditEventSSE = {
      type: 'SUPERVISOR_START',
      timestamp: '2025-10-23T10:30:00.000Z',
      sessionId: 'session123',
      details: { message: 'Processing user query' }
    }

    const event = parseSSEEvent(sseData)

    expect(event.timestamp).toBeInstanceOf(Date)
    expect(event.timestamp.toISOString()).toBe('2025-10-23T10:30:00.000Z')
  })

  it('generates unique ID via crypto.randomUUID()', () => {
    const sseData: AuditEventSSE = {
      type: 'CREW_START',
      timestamp: '2025-10-23T10:30:01.000Z',
      sessionId: 'session123',
      details: { crewName: 'disease_ontology' }
    }

    const event = parseSSEEvent(sseData)

    // crypto.randomUUID is mocked in setup.ts to return a fixed UUID
    expect(event.id).toBe('123e4567-e89b-12d3-a456-426614174000')
    expect(event.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  })

  it('preserves type and sessionId from SSE data', () => {
    const sseData: AuditEventSSE = {
      type: 'TOOL_START',
      timestamp: '2025-10-23T10:30:02.000Z',
      sessionId: 'session456',
      details: { toolName: 'sql_query_tool', friendlyName: 'Searching database...' }
    }

    const event = parseSSEEvent(sseData)

    expect(event.type).toBe('TOOL_START')
    expect(event.sessionId).toBe('session456')
  })

  it('handles all 10 event types correctly', () => {
    const eventTypes: AuditEventType[] = [
      'SUPERVISOR_START',
      'SUPERVISOR_DISPATCH',
      'CREW_START',
      'AGENT_COMPLETE',
      'TOOL_START',
      'TOOL_COMPLETE',
      'LLM_CALL',
      'SUPERVISOR_RESULT',
      'SUPERVISOR_COMPLETE',
      'SUPERVISOR_ERROR'
    ]

    eventTypes.forEach(type => {
      const sseData: AuditEventSSE = {
        type,
        timestamp: '2025-10-23T10:30:00.000Z',
        sessionId: 'session123',
        details: {}
      }

      const event = parseSSEEvent(sseData)

      expect(event.type).toBe(type)
      expect(event.timestamp).toBeInstanceOf(Date)
      expect(event.sessionId).toBe('session123')
      expect(event.id).toBeDefined()
    })
  })

  it('preserves event details as-is', () => {
    const details = {
      domainName: 'internal_db_domain',
      stepNumber: 1,
      totalSteps: 1,
      isParallel: false
    }

    const sseData: AuditEventSSE = {
      type: 'SUPERVISOR_DISPATCH',
      timestamp: '2025-10-23T10:30:00.000Z',
      sessionId: 'session123',
      details
    }

    const event = parseSSEEvent(sseData)

    expect(event.details).toEqual(details)
  })
})

// ===================================================================
// T013: Test formatAuditEvent
// ===================================================================
describe('formatAuditEvent (T013)', () => {
  it('returns correct prefix + label format', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: { message: 'Processing user query' } as SupervisorStartDetails
    }

    const formatted = formatAuditEvent(event)

    expect(formatted).toBe('[SUPERVISOR] Processing user query')
    expect(formatted).toMatch(/^\[.*\] .+$/) // Matches "[PREFIX] label" pattern
  })

  it('handles all 10 event types', () => {
    const testCases: Array<{ type: AuditEventType, details: any, expectedPattern: RegExp }> = [
      {
        type: 'SUPERVISOR_START',
        details: { message: 'Processing' },
        expectedPattern: /^\[SUPERVISOR\] Processing$/
      },
      {
        type: 'SUPERVISOR_DISPATCH',
        details: { domainName: 'internal_db_domain', stepNumber: 1, totalSteps: 1 },
        expectedPattern: /^\[SUPERVISOR\] Dispatching domain:/
      },
      {
        type: 'CREW_START',
        details: { crewName: 'test_crew' },
        expectedPattern: /^\[CREW\] Starting crew:/
      },
      {
        type: 'AGENT_COMPLETE',
        details: { agentRole: 'test_agent' },
        expectedPattern: /^\[AGENT\] Agent completed:/
      },
      {
        type: 'TOOL_START',
        details: { toolName: 'sql_query_tool', friendlyName: 'Searching...' },
        expectedPattern: /^\[TOOL\] Searching\.\.\./
      },
      {
        type: 'TOOL_COMPLETE',
        details: { toolName: 'sql_query_tool', friendlyName: 'Complete' },
        expectedPattern: /^\[TOOL\] Complete$/
      },
      {
        type: 'LLM_CALL',
        details: { message: 'Thinking...' },
        expectedPattern: /^\[LLM\] Thinking\.\.\./
      },
      {
        type: 'SUPERVISOR_RESULT',
        details: { domainName: 'internal_db_domain', stepNumber: 1, hasError: false },
        expectedPattern: /^\[SUPERVISOR\] Results from/
      },
      {
        type: 'SUPERVISOR_COMPLETE',
        details: { message: 'Completed', totalSteps: 1 },
        expectedPattern: /^\[SUPERVISOR\] Completed/
      },
      {
        type: 'SUPERVISOR_ERROR',
        details: { error: 'Test error' },
        expectedPattern: /^\[SUPERVISOR ERROR\] Supervisor error:/
      }
    ]

    testCases.forEach(({ type, details, expectedPattern }) => {
      const event: AuditEvent = {
        id: '123',
        type,
        timestamp: new Date(),
        sessionId: 'session123',
        details
      }

      const formatted = formatAuditEvent(event)
      expect(formatted).toMatch(expectedPattern)
    })
  })

  it('includes query details for TOOL_START with SQL query', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'TOOL_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        toolName: 'sql_query_tool',
        friendlyName: 'Searching database...',
        toolArgs: {
          query: "SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'"
        }
      } as ToolStartDetails
    }

    const formatted = formatAuditEvent(event)

    expect(formatted).toContain('Searching database...')
    expect(formatted).toContain('Query:')
    expect(formatted).toContain('SELECT * FROM ontology_terms')
  })

  it('includes API details for TOOL_START with REST API call', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'TOOL_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        toolName: 'rest_api_call',
        friendlyName: 'Calling external API...',
        toolArgs: {
          url: 'https://api.example.com/endpoint',
          method: 'GET'
        }
      } as ToolStartDetails
    }

    const formatted = formatAuditEvent(event)

    expect(formatted).toContain('Calling external API...')
    expect(formatted).toContain('GET https://api.example.com/endpoint')
  })
})

// ===================================================================
// T014: Test getEventPrefix
// ===================================================================
describe('getEventPrefix (T014)', () => {
  it('returns text labels without emojis', () => {
    const prefixes = [
      getEventPrefix('SUPERVISOR_START'),
      getEventPrefix('CREW_START'),
      getEventPrefix('AGENT_COMPLETE'),
      getEventPrefix('TOOL_START'),
      getEventPrefix('LLM_CALL'),
      getEventPrefix('SUPERVISOR_ERROR')
    ]

    // Verify no emojis (emoji regex pattern)
    const emojiRegex = /[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]/u

    prefixes.forEach(prefix => {
      expect(prefix).not.toMatch(emojiRegex)
      expect(prefix).toMatch(/^\[.*\]$/) // Matches [TEXT] format
    })
  })

  it('returns [SUPERVISOR] for supervisor events', () => {
    expect(getEventPrefix('SUPERVISOR_START')).toBe('[SUPERVISOR]')
    expect(getEventPrefix('SUPERVISOR_DISPATCH')).toBe('[SUPERVISOR]')
    expect(getEventPrefix('SUPERVISOR_RESULT')).toBe('[SUPERVISOR]')
    expect(getEventPrefix('SUPERVISOR_COMPLETE')).toBe('[SUPERVISOR]')
  })

  it('returns [SUPERVISOR ERROR] for error events', () => {
    expect(getEventPrefix('SUPERVISOR_ERROR')).toBe('[SUPERVISOR ERROR]')
  })

  it('returns [CREW] for crew events', () => {
    expect(getEventPrefix('CREW_START')).toBe('[CREW]')
  })

  it('returns [AGENT] for agent events', () => {
    expect(getEventPrefix('AGENT_COMPLETE')).toBe('[AGENT]')
  })

  it('returns [TOOL] for tool events', () => {
    expect(getEventPrefix('TOOL_START')).toBe('[TOOL]')
    expect(getEventPrefix('TOOL_COMPLETE')).toBe('[TOOL]')
  })

  it('returns [LLM] for LLM events', () => {
    expect(getEventPrefix('LLM_CALL')).toBe('[LLM]')
  })

  it('handles all 10 event types', () => {
    const eventTypes: AuditEventType[] = [
      'SUPERVISOR_START',
      'SUPERVISOR_DISPATCH',
      'CREW_START',
      'AGENT_COMPLETE',
      'TOOL_START',
      'TOOL_COMPLETE',
      'LLM_CALL',
      'SUPERVISOR_RESULT',
      'SUPERVISOR_COMPLETE',
      'SUPERVISOR_ERROR',
      'DOMAIN_PLAN_CREATED',
      'DOMAIN_PLANNING',
      'DOMAIN_EXECUTION_START',
      'DOMAIN_COMPLETED',
      'DOMAIN_CATEGORY_ERROR',
      'DOMAIN_SKIPPED'
    ]

    const expectedPrefixes: Record<AuditEventType, string> = {
      'SUPERVISOR_START': '[SUPERVISOR]',
      'SUPERVISOR_DISPATCH': '[SUPERVISOR]',
      'SUPERVISOR_RESULT': '[SUPERVISOR]',
      'SUPERVISOR_COMPLETE': '[SUPERVISOR]',
      'SUPERVISOR_ERROR': '[SUPERVISOR ERROR]',
      'CREW_START': '[CREW]',
      'AGENT_COMPLETE': '[AGENT]',
      'TOOL_START': '[TOOL]',
      'TOOL_COMPLETE': '[TOOL]',
      'LLM_CALL': '[LLM]',
      'DOMAIN_PLAN_CREATED': '[DOMAIN]',
      'DOMAIN_PLANNING': '[DOMAIN]',
      'DOMAIN_EXECUTION_START': '[DOMAIN]',
      'DOMAIN_COMPLETED': '[DOMAIN]',
      'DOMAIN_CATEGORY_ERROR': '[DOMAIN ERROR]',
      'DOMAIN_SKIPPED': '[DOMAIN]'
    }

    eventTypes.forEach(type => {
      expect(getEventPrefix(type)).toBe(expectedPrefixes[type])
    })
  })
})

// ===================================================================
// T015: Test getEventLabel
// ===================================================================
describe('getEventLabel (T015)', () => {
  it('formats SUPERVISOR_START with message', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: { message: 'Processing user query' } as SupervisorStartDetails
    }

    expect(getEventLabel(event)).toBe('Processing user query')
  })

  it('formats SUPERVISOR_DISPATCH with crew name and step numbers', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_DISPATCH',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        domainName: 'internal_db_domain',
        stepNumber: 1,
        totalSteps: 2
      } as SupervisorDispatchDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Dispatching domain: Database Search')
    expect(label).toContain('step 1/2')
  })

  it('formats SUPERVISOR_DISPATCH with parallel execution flag', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_DISPATCH',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        domainName: 'external_api_domain',
        stepNumber: 1,
        totalSteps: 3,
        isParallel: true
      } as SupervisorDispatchDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('parallel execution')
  })

  it('formats CREW_START with crew name', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'CREW_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: { crewName: 'disease_ontology' } as CrewStartDetails
    }

    expect(getEventLabel(event)).toContain('Starting crew: disease_ontology')
  })

  it('formats CREW_START with crew display name if available', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'CREW_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        crewName: 'disease_ontology',
        crewDisplayName: 'Disease Ontology Crew'
      } as CrewStartDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Disease Ontology Crew')
    expect(label).not.toContain('disease_ontology')
  })

  it('formats CREW_START with agents list', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'CREW_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        crewName: 'disease_ontology',
        agents: ['disease_ontology_agent', 'lookup_agent']
      } as CrewStartDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('with agents:')
    expect(label).toContain('disease_ontology_agent')
    expect(label).toContain('lookup_agent')
  })

  it('formats AGENT_COMPLETE with agent role', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'AGENT_COMPLETE',
      timestamp: new Date(),
      sessionId: 'session123',
      details: { agentRole: 'disease_ontology_agent' } as AgentCompleteDetails
    }

    expect(getEventLabel(event)).toContain('Agent completed: disease_ontology_agent')
  })

  it('formats AGENT_COMPLETE with display name if available', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'AGENT_COMPLETE',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        agentRole: 'disease_ontology_agent',
        agentDisplayName: 'Disease Ontology Agent'
      } as AgentCompleteDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Disease Ontology Agent')
    expect(label).not.toContain('disease_ontology_agent')
  })

  it('formats TOOL_START with friendly name', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'TOOL_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        toolName: 'sql_query_tool',
        friendlyName: 'Searching database...'
      } as ToolStartDetails
    }

    expect(getEventLabel(event)).toBe('Searching database...')
  })

  it('formats TOOL_START with SQL query details', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'TOOL_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        toolName: 'sql_query_tool',
        friendlyName: 'Searching database...',
        toolArgs: {
          query: "SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'"
        }
      } as ToolStartDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Query:')
    expect(label).toContain('SELECT * FROM ontology_terms')
  })

  it('formats TOOL_START with API params', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'TOOL_START',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        toolName: 'rest_api_call',
        friendlyName: 'Calling external API...',
        toolArgs: {
          url: 'https://api.example.com/genes/ENT1',
          method: 'POST'
        }
      } as ToolStartDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('POST https://api.example.com/genes/ENT1')
  })

  it('formats TOOL_COMPLETE with friendly name', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'TOOL_COMPLETE',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        toolName: 'sql_query_tool',
        friendlyName: 'Database search complete'
      } as ToolCompleteDetails
    }

    expect(getEventLabel(event)).toBe('Database search complete')
  })

  it('formats TOOL_COMPLETE with failure indication', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'TOOL_COMPLETE',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        toolName: 'rest_api_call',
        friendlyName: 'API call complete',
        success: false
      } as ToolCompleteDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('API call complete')
    expect(label).toContain('(failed)')
  })

  it('formats LLM_CALL with default message', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'LLM_CALL',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {} as LLMCallDetails
    }

    expect(getEventLabel(event)).toBe('Thinking...')
  })

  it('formats LLM_CALL with custom message and agent', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'LLM_CALL',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        message: 'Analyzing ontology terms',
        agent: 'disease_ontology_agent'
      } as LLMCallDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Analyzing ontology terms')
    expect(label).toContain('(disease_ontology_agent)')
  })

  it('formats SUPERVISOR_RESULT with domain name and step', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_RESULT',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        domainName: 'internal_db_domain',
        stepNumber: 1,
        hasError: false
      } as SupervisorResultDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Results from Database Search')
    expect(label).toContain('(step 1)')
  })

  it('formats SUPERVISOR_RESULT with error indication', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_RESULT',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        domainName: 'external_api_domain',
        stepNumber: 2,
        hasError: true
      } as SupervisorResultDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('⚠️ with issues')
  })

  it('formats SUPERVISOR_COMPLETE with message and total steps', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_COMPLETE',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        message: 'Query completed successfully',
        totalSteps: 3
      } as SupervisorCompleteDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Query completed successfully')
    expect(label).toContain('(3 steps executed)')
  })

  it('formats SUPERVISOR_ERROR with error message', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_ERROR',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        error: 'Database connection failed'
      } as SupervisorErrorDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('Supervisor error:')
    expect(label).toContain('Database connection failed')
  })

  it('formats SUPERVISOR_ERROR with crew context', () => {
    const event: AuditEvent = {
      id: '123',
      type: 'SUPERVISOR_ERROR',
      timestamp: new Date(),
      sessionId: 'session123',
      details: {
        error: 'Agent timeout',
        crewName: 'disease_ontology'
      } as SupervisorErrorDetails
    }

    const label = getEventLabel(event)

    expect(label).toContain('in disease_ontology')
    expect(label).toContain('Agent timeout')
  })

  it('handles all 10 event types', () => {
    const testEvents: Array<{ type: AuditEventType, details: any }> = [
      { type: 'SUPERVISOR_START', details: { message: 'Test' } },
      { type: 'SUPERVISOR_DISPATCH', details: { crewName: 'test', stepNumber: 0, totalSteps: 1 } },
      { type: 'CREW_START', details: { crewName: 'test' } },
      { type: 'AGENT_COMPLETE', details: { agentRole: 'test_agent' } },
      { type: 'TOOL_START', details: { toolName: 'test_tool' } },
      { type: 'TOOL_COMPLETE', details: { toolName: 'test_tool' } },
      { type: 'LLM_CALL', details: {} },
      { type: 'SUPERVISOR_RESULT', details: { crewName: 'test', stepNumber: 0 } },
      { type: 'SUPERVISOR_COMPLETE', details: { message: 'Done', totalSteps: 1 } },
      { type: 'SUPERVISOR_ERROR', details: { error: 'Test error' } }
    ]

    testEvents.forEach(({ type, details }) => {
      const event: AuditEvent = {
        id: '123',
        type,
        timestamp: new Date(),
        sessionId: 'session123',
        details
      }

      const label = getEventLabel(event)

      expect(label).toBeDefined()
      expect(label.length).toBeGreaterThan(0)
      expect(label).not.toBe('Unknown event')
    })
  })
})

// ===================================================================
// T016: Test getEventSeverity
// ===================================================================
describe('getEventSeverity (T016)', () => {
  it('returns "error" for SUPERVISOR_ERROR', () => {
    expect(getEventSeverity('SUPERVISOR_ERROR')).toBe('error')
  })

  it('returns "success" for *_COMPLETE events', () => {
    expect(getEventSeverity('AGENT_COMPLETE')).toBe('success')
    expect(getEventSeverity('TOOL_COMPLETE')).toBe('success')
    expect(getEventSeverity('SUPERVISOR_COMPLETE')).toBe('success')
  })

  it('returns "info" for SUPERVISOR_START', () => {
    expect(getEventSeverity('SUPERVISOR_START')).toBe('info')
  })

  it('returns "info" for SUPERVISOR_DISPATCH', () => {
    expect(getEventSeverity('SUPERVISOR_DISPATCH')).toBe('info')
  })

  it('returns "info" for CREW_START', () => {
    expect(getEventSeverity('CREW_START')).toBe('info')
  })

  it('returns "info" for TOOL_START', () => {
    expect(getEventSeverity('TOOL_START')).toBe('info')
  })

  it('returns "info" for LLM_CALL', () => {
    expect(getEventSeverity('LLM_CALL')).toBe('info')
  })

  it('returns "success" for SUPERVISOR_RESULT', () => {
    expect(getEventSeverity('SUPERVISOR_RESULT')).toBe('success')
  })

  it('handles all event types', () => {
    const expectedSeverities: Record<AuditEventType, AuditSeverity> = {
      'SUPERVISOR_START': 'info',
      'SUPERVISOR_DISPATCH': 'info',
      'CREW_START': 'info',
      'AGENT_COMPLETE': 'success',
      'TOOL_START': 'info',
      'TOOL_COMPLETE': 'success',
      'LLM_CALL': 'info',
      'SUPERVISOR_RESULT': 'success',
      'SUPERVISOR_COMPLETE': 'success',
      'SUPERVISOR_ERROR': 'error',
      'DOMAIN_PLAN_CREATED': 'info',
      'DOMAIN_PLANNING': 'info',
      'DOMAIN_EXECUTION_START': 'info',
      'DOMAIN_COMPLETED': 'success',
      'DOMAIN_CATEGORY_ERROR': 'error',
      'DOMAIN_SKIPPED': 'info'
    }

    Object.entries(expectedSeverities).forEach(([type, severity]) => {
      expect(getEventSeverity(type as AuditEventType)).toBe(severity)
    })
  })

  it('returns "warning" for failed tool completion', () => {
    expect(getEventSeverity('TOOL_COMPLETE', { success: false })).toBe('warning')
  })

  it('returns "warning" for partial domain success', () => {
    expect(getEventSeverity('DOMAIN_COMPLETED', { success: 1, total: 3 })).toBe('warning')
  })

  it('returns "warning" when supervisor result reports errors', () => {
    expect(getEventSeverity('SUPERVISOR_RESULT', { hasError: true })).toBe('warning')
  })
})
