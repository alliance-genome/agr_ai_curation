import { createRef } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ChatMessageList from './ChatMessageList'
import type { ChatCssVariables, Message } from './types'

describe('chat review readiness', () => {
  it('renders ledger readiness separately from model prose', () => {
    const message: Message = {
      role: 'assistant', content: 'Everything is validated!', timestamp: new Date(), turnId: 'turn',
      validationReview: { pending_count: 1, results: [{ result_ref: 'saved', episode_id: 'episode', ready: false }] },
    }
    const props = {
      messages: [message], isLoading: false, progressMessage: '', flowProgress: null,
      messagesEndRef: createRef<HTMLDivElement>(), chatCssVariables: {} as ChatCssVariables,
      sessionId: 'session', onCopyMessage: vi.fn(),
      onFeedbackClick: vi.fn(), onOpenCurationWorkspace: vi.fn().mockResolvedValue(null),
      onReviewAndCurateOpened: vi.fn(), onUnsupportedEvidenceReview: vi.fn(),
    }
    const view = render(<MemoryRouter><ChatMessageList {...props} /></MemoryRouter>)
    expect(screen.getByRole('status')).toHaveTextContent('Validation review pending')
    expect(screen.getByRole('status')).toHaveTextContent('Final export and curation preparation are unavailable')
    view.rerender(<MemoryRouter><ChatMessageList {...props} messages={[{ ...message,
      validationReview: { pending_count: 0, results: [{ result_ref: 'saved', episode_id: 'episode', ready: true }] },
    }]} /></MemoryRouter>)
    expect(screen.getByRole('status')).toHaveTextContent('Validation review complete')
    view.rerender(<MemoryRouter><ChatMessageList {...props} messages={[{ ...message, validationReviewSnapshot: true }]} /></MemoryRouter>)
    expect(screen.getByRole('status')).toHaveTextContent('Review status at the time of this response: Pending.')
    expect(screen.getByRole('status')).not.toHaveTextContent('are unavailable')
  })
  it('preserves the user role and exact copied message in a separate action row', () => {
    const onCopyMessage = vi.fn()
    const content = 'A long question\nwith a second line'
    render(<MemoryRouter><ChatMessageList
      messages={[{ role: 'user', content, timestamp: new Date() }]}
      isLoading={false} progressMessage="" flowProgress={null}
      messagesEndRef={createRef<HTMLDivElement>()} chatCssVariables={{} as ChatCssVariables}
      sessionId="session" onCopyMessage={onCopyMessage} onFeedbackClick={vi.fn()}
      onOpenCurationWorkspace={vi.fn()} onReviewAndCurateOpened={vi.fn()} onUnsupportedEvidenceReview={vi.fn()}
    /></MemoryRouter>)
    expect(screen.getByText('You')).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Copy your message' })
    expect(button.parentElement).toHaveClass('user-message-actions')
    expect(button.closest('.message-content')).toBeNull()
    fireEvent.click(button)
    expect(onCopyMessage).toHaveBeenCalledWith(content)
  })

})
