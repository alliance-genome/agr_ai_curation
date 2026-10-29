import { useState } from 'react'
import FeedbackDialog from './FeedbackDialog'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@/test/test-utils'

import MessageActions from './MessageActions'
import { copyText } from './copyText'
vi.mock('./copyText', () => ({ copyText: vi.fn().mockResolvedValue(undefined) }))

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
    vi.mocked(copyText).mockReset().mockResolvedValue(undefined)
  })

  function openAgentStudioMenu() {
    fireEvent.click(screen.getByRole('button', { name: 'Show message actions' }))
    fireEvent.click(screen.getByRole('button', { name: /more actions/i }))
    fireEvent.click(screen.getByRole('menuitem', { name: /open in agent studio/i }))
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

  it('reveals ordered actions and puts debug copy third in the menu', () => {
    render(<MessageActions messageContent="A message" traceId="trace-456" onFeedbackClick={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show message actions' }))
    const actions = screen.getAllByRole('button').map((button) => button.getAttribute('aria-label'))
    expect(actions).toEqual(['Hide message actions', 'copy message', 'more actions'])
    expect(screen.queryByRole('button', { name: /copy debug id/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /more actions/i }))
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
      'Provide Feedback', 'Open in Agent Studio', 'Copy Debug ID',
    ])
  })
  it('keeps hidden controls out of the accessible tree and supports keyboard dismissal', async () => {
    const user = userEvent.setup()
    render(<MessageActions messageContent="message" onFeedbackClick={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'copy message' })).not.toBeInTheDocument()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Show message actions' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'copy message' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Show message actions' })).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'copy message' })).not.toBeInTheDocument()
  })

  it('closes the menu on Escape before collapsing the toolbar on a second Escape', async () => {
    const user = userEvent.setup()
    render(<MessageActions messageContent="message" onFeedbackClick={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Show message actions' }))
    await user.click(screen.getByRole('button', { name: 'more actions' }))
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'more actions' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('button', { name: 'copy message' })).not.toBeInTheDocument()
  })

  it('copies exact values, closes debug menu on selection, and retains feedback callback', async () => {
    const user = userEvent.setup()
    const feedback = vi.fn()
    render(<MessageActions messageContent={'quoted\nmessage'} traceId="trace-123" onFeedbackClick={feedback} />)
    await user.click(screen.getByRole('button', { name: 'Show message actions' }))
    await user.click(screen.getByRole('button', { name: 'copy message' }))
    expect(copyText).toHaveBeenLastCalledWith('quoted\nmessage')
    await user.click(screen.getByRole('button', { name: 'more actions' }))
    await user.click(screen.getByRole('menuitem', { name: 'Copy Debug ID' }))
    expect(copyText).toHaveBeenLastCalledWith('trace-123')
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'more actions' }))
    await user.click(screen.getByRole('menuitem', { name: 'Provide Feedback' }))
    await waitFor(() => expect(feedback).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
  })

  it('dismisses the debug menu even when copying fails', async () => {
    const user = userEvent.setup()
    const failure = new Error('Clipboard denied')
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(copyText).mockRejectedValue(failure)
    render(<MessageActions messageContent="message" traceId="trace" onFeedbackClick={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Show message actions' }))
    await user.click(screen.getByRole('button', { name: 'more actions' }))
    await user.click(screen.getByRole('menuitem', { name: 'Copy Debug ID' }))
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
    expect(logged).toHaveBeenCalledWith('Failed to copy trace ID:', failure)
    logged.mockRestore()
  })

  it('preserves optional review and omits debug without a trace', async () => {
    const user = userEvent.setup()
    render(<MessageActions messageContent="message" reviewAndCurateTarget={{ sessionId: 'curation-session' }} onFeedbackClick={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Show message actions' }))
    expect(screen.getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Hide message actions', 'Review & Curate', 'copy message', 'more actions',
    ])
    await user.click(screen.getByRole('button', { name: 'more actions' }))
    expect(screen.queryByRole('menuitem', { name: 'Copy Debug ID' })).not.toBeInTheDocument()
  })

  it('uses separate toolbar and menu IDs for each message', async () => {
    const user = userEvent.setup()
    render(<><MessageActions messageContent="one" onFeedbackClick={vi.fn()} /><MessageActions messageContent="two" onFeedbackClick={vi.fn()} /></>)
    const toggles = screen.getAllByRole('button', { name: 'Show message actions' })
    expect(toggles[0].getAttribute('aria-controls')).not.toBe(toggles[1].getAttribute('aria-controls'))
    await user.click(toggles[0])
    await user.click(screen.getByRole('button', { name: 'more actions' }))
    const id = screen.getByRole('button', { name: 'more actions', hidden: true }).getAttribute('aria-controls')
    expect(document.getElementById(id!)).toBeInTheDocument()
  })

  it('opens the real modeless feedback field and lets Escape pass through a collapsed toolbar', async () => {
    const user = userEvent.setup()
    function FeedbackFixture() {
      const [open, setOpen] = useState(false)
      return <>
        <MessageActions messageContent="message" onFeedbackClick={() => setOpen(true)} />
        <FeedbackDialog open={open} onClose={() => setOpen(false)} sessionId="session" onSubmit={async () => {}} />
      </>
    }
    render(<FeedbackFixture />)
    await user.click(screen.getByRole('button', { name: 'Show message actions' }))
    await user.click(screen.getByRole('button', { name: 'more actions' }))
    await user.click(screen.getByRole('menuitem', { name: 'Provide Feedback' }))
    const field = await screen.findByPlaceholderText(/enter your detailed feedback here/i)
    await waitFor(() => expect(field).toHaveFocus())
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Show message actions' })
    fireEvent.focus(toggle)
    await user.keyboard('{Escape}')
    toggle.focus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

})
