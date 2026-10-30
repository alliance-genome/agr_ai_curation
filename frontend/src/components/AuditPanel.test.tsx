import { ThemeProvider } from '@mui/material/styles'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import AuditPanel from './AuditPanel'

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { uid: 'user-1' } }),
}))

function renderAuditPanel(isStreaming: boolean, sseEvents: any[] = []) {
  return render(
    <ThemeProvider theme={theme}>
      <AuditPanel
        sessionId="session-1"
        sseEvents={sseEvents}
        eventStreamVersion={0}
        durableReconciliationVersion={0}
        isStreaming={isStreaming}
        onStop={vi.fn()}
      />
    </ThemeProvider>,
  )
}

describe('AuditPanel active run indicator', () => {
  it('shows a status indicator while a chat or flow run is streaming', () => {
    renderAuditPanel(true)

    expect(screen.getByRole('status', { name: 'Curation run in progress' })).toBeInTheDocument()
    expect(screen.getByText('Running')).toBeInTheDocument()
  })

  it('hides the status indicator when no run is streaming', () => {
    renderAuditPanel(false)

    expect(screen.queryByRole('status', { name: 'Curation run in progress' })).not.toBeInTheDocument()
  })
})


describe('AuditPanel review activity rows', () => {
  it.each([true, false])('gates latest-row animation on live streaming=%s', (isStreaming) => {
    const { container } = renderAuditPanel(isStreaming, [{ type: 'AGENT_GENERATING', session_id: 'session-1',
      timestamp: new Date().toISOString(), details: { agentRole: 'supervisor', message: 'Reviewing findings' } }])
    expect(Boolean(container.querySelector('.audit-animated-dots'))).toBe(isStreaming)
  })
})
