import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AuditEventItem from './AuditEventItem'
import { getEventSeverity } from '../utils/auditHelpers'
import type { AuditEvent } from '../types/AuditEvent'

const event: AuditEvent = { id: 'review', sessionId: 'session', timestamp: new Date(),
  type: 'AGENT_GENERATING', details: { agentRole: 'supervisor', message: 'Reviewing findings' } }

describe('review progress indicator', () => {
  it('animates only the live latest processing row', () => {
    const { container, rerender } = render(<AuditEventItem event={event} isLatest />)
    expect(container.querySelector('.audit-animated-dots')).not.toBeNull()
    rerender(<AuditEventItem event={event} isLatest={false} />)
    expect(container.querySelector('.audit-animated-dots')).toBeNull()
    rerender(<AuditEventItem event={{ ...event, type: 'AGENT_COMPLETE', details: {
      agentRole: 'supervisor', message: 'Supervisor review ended with unresolved work', success: false,
    } }} isLatest />)
    expect(container.querySelector('.audit-animated-dots')).toBeNull()
    expect(container.textContent).toContain('ended with unresolved work')
  })
  it('distinguishes a stopped dispatch from success and failure', () => {
    expect(getEventSeverity('TOOL_COMPLETE', { severity: 'warning' })).toBe('warning')
    expect(getEventSeverity('TOOL_COMPLETE', { hasError: true })).toBe('error')
    expect(getEventSeverity('TOOL_COMPLETE', {})).toBe('success')
  })
  it('honors reduced motion', () => {
    expect(document.getElementById('audit-animated-dots-style')?.textContent)
      .toContain('@media (prefers-reduced-motion: reduce)')
  })
})
