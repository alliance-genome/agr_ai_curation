import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@/test/test-utils'
import { ThemeProvider, alpha } from '@mui/material/styles'
import { createAppTheme } from '@/theme'

import MessageActions from './MessageActions'

const mockNavigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

describe('MessageActions', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
  })

  function openAgentStudioMenu() {
    fireEvent.click(screen.getByRole('button', { name: /more actions/i }))
    fireEvent.click(screen.getByRole('menuitem', { name: /open in agent studio/i }))
  }

  function renderWithAppTheme(ui: ReactElement) {
    const theme = createAppTheme('light')

    return {
      theme,
      ...render(
        <ThemeProvider theme={theme}>
          {ui}
        </ThemeProvider>
      ),
    }
  }

  it('navigates to Agent Studio with both durable session and trace params', () => {
    render(
      <MessageActions
        messageContent="Investigate this assistant turn"
        sessionId="session-123"
        traceId="trace-456"
        onFeedbackClick={vi.fn()}
      />
    )

    openAgentStudioMenu()

    expect(mockNavigate).toHaveBeenCalledWith('/agent-studio?session_id=session-123&trace_id=trace-456')
  })

  it('navigates with only the durable session param when trace context is missing', () => {
    render(
      <MessageActions
        messageContent="Investigate this assistant turn"
        sessionId="session-123"
        onFeedbackClick={vi.fn()}
      />
    )

    openAgentStudioMenu()

    expect(mockNavigate).toHaveBeenCalledWith('/agent-studio?session_id=session-123')
  })

  it('navigates with only the trace param when durable session context is missing', () => {
    render(
      <MessageActions
        messageContent="Investigate this assistant turn"
        traceId="trace-456"
        onFeedbackClick={vi.fn()}
      />
    )

    openAgentStudioMenu()

    expect(mockNavigate).toHaveBeenCalledWith('/agent-studio?trace_id=trace-456')
  })

  it('keeps the debug copy action readable on assistant message chrome', () => {
    const { theme } = renderWithAppTheme(
      <MessageActions
        messageContent="Investigate this assistant turn"
        traceId="trace-456"
        onFeedbackClick={vi.fn()}
      />
    )

    expect(screen.getByRole('button', { name: /copy debug id/i })).toHaveStyle({
      color: alpha(theme.palette.secondary.contrastText, 0.72),
    })
  })
})
