import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Chat from '../../components/Chat'

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { email: 'curator@example.org' },
  }),
}))

function renderChat(props?: Partial<ComponentProps<typeof Chat>>) {
  const sendMessage = props?.sendMessage ?? vi.fn().mockResolvedValue(undefined)
  const mergedProps: ComponentProps<typeof Chat> = {
    sessionId: 'session-1',
    events: [],
    isLoading: false,
    sendMessage,
    onSessionChange: vi.fn(),
    ...props,
  }

  return {
    ...render(
      <MemoryRouter>
        <Chat {...mergedProps} />
      </MemoryRouter>
    ),
    sendMessage,
  }
}

describe('Chat persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({}),
    } as Response)
  })

  it('persists pending chat data on unmount and restores it on remount', async () => {
    localStorage.setItem('chat-session-id', 'session-1')
    const { unmount, sendMessage } = renderChat({ sessionId: 'session-1' })

    const input = screen.getByPlaceholderText('Type your message...')
    fireEvent.change(input, { target: { value: 'Persist me across navigation' } })
    fireEvent.keyPress(input, { key: 'Enter', code: 'Enter', charCode: 13 })

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith('Persist me across navigation', 'session-1')
    })

    // Simulate navigating away from Home before debounce timer naturally fires.
    unmount()

    const storedRaw = localStorage.getItem('chat-messages')
    expect(storedRaw).not.toBeNull()
    const stored = JSON.parse(storedRaw || '{}')
    expect(stored.session_id).toBe('session-1')
    expect(stored.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: 'user',
          content: 'Persist me across navigation',
        }),
      ])
    )

    renderChat({ sessionId: 'session-1' })

    expect(screen.getByText('Persist me across navigation')).toBeInTheDocument()
  })

  it('does not delete stored messages when session id mismatches', () => {
    localStorage.setItem('chat-session-id', 'session-2')
    localStorage.setItem(
      'chat-messages',
      JSON.stringify({
        session_id: 'session-1',
        messages: [
          {
            role: 'user',
            content: 'Old session message',
            timestamp: new Date().toISOString(),
          },
        ],
      })
    )

    renderChat({ sessionId: 'session-2' })

    expect(localStorage.getItem('chat-messages')).not.toBeNull()
  })
})
