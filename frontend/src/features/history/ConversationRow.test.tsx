import { describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '@mui/material/styles'

import { render, screen, userEvent } from '@/test/test-utils'
import type { ChatHistorySessionSummary } from '@/services/chatHistoryApi'
import { createAppTheme } from '@/theme'

import ConversationRow from './ConversationRow'

const LONG_TITLE = 'Allele phenotype extraction: FBal0193541 and FBal0193542 from the supplementary tables of Chen et al. 2024, including RNAi lines'

function buildSession(overrides: Partial<ChatHistorySessionSummary> = {}): ChatHistorySessionSummary {
  return {
    session_id: 'session-1',
    chat_kind: 'assistant_chat',
    title: 'TP53 evidence review',
    active_document_id: null,
    created_at: '2026-04-20T09:00:00Z',
    updated_at: '2026-04-20T09:15:00Z',
    last_message_at: '2026-04-20T09:14:00Z',
    recent_activity_at: '2026-04-20T09:15:00Z',
    ...overrides,
  }
}

function renderRow(
  overrides: Partial<ChatHistorySessionSummary> = {},
  props: Partial<Parameters<typeof ConversationRow>[0]> = {},
) {
  const handlers = {
    onCopySessionId: vi.fn(),
    onDelete: vi.fn(),
    onRename: vi.fn(),
    onRestore: vi.fn(),
    onSelectChange: vi.fn(),
    onToggleExpand: vi.fn(),
  }

  render(
    <ThemeProvider theme={createAppTheme('light')}>
      <ul>
        <ConversationRow
          isExpanded={false}
          isSelected={false}
          session={buildSession(overrides)}
          {...handlers}
          {...props}
        >
          <div>Expanded panel</div>
        </ConversationRow>
      </ul>
    </ThemeProvider>,
  )

  return handlers
}

describe('ConversationRow', () => {
  it('exposes the row as one focusable button named by the title with kind and time as description', () => {
    renderRow({ active_document_id: 'doc-1' })

    const row = screen.getByRole('button', { name: 'TP53 evidence review' })
    expect(row).toHaveAttribute('tabindex', '0')
    expect(row).toHaveAttribute('aria-expanded', 'false')
    expect(row).toHaveAccessibleDescription(/Assistant/)
    expect(row).toHaveAccessibleDescription(/Document linked/)
    expect(screen.getByText('Assistant')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select TP53 evidence review' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Resume chat' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show transcript' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: 'More actions for TP53 evidence review' })).toHaveAttribute('aria-haspopup', 'menu')
    expect(screen.queryByText('session-1')).not.toBeInTheDocument()
    expect(screen.queryByText('Expanded panel')).not.toBeInTheDocument()
  })

  it('toggles expansion with Enter, Space, or a click and leaves selection to the checkbox', async () => {
    const user = userEvent.setup()
    const handlers = renderRow()
    const row = screen.getByRole('button', { name: 'TP53 evidence review' })

    row.focus()
    await user.keyboard('{Enter}')
    expect(handlers.onToggleExpand).toHaveBeenCalledTimes(1)
    expect(handlers.onSelectChange).not.toHaveBeenCalled()

    await user.keyboard(' ')
    expect(handlers.onToggleExpand).toHaveBeenCalledTimes(2)
    expect(handlers.onSelectChange).not.toHaveBeenCalled()

    await user.click(row)
    expect(handlers.onToggleExpand).toHaveBeenCalledTimes(3)

    await user.click(screen.getByRole('checkbox', { name: 'Select TP53 evidence review' }))
    expect(handlers.onSelectChange).toHaveBeenLastCalledWith(true)
    expect(handlers.onToggleExpand).toHaveBeenCalledTimes(3)
  })

  it('renders the expanded panel and the rotated chevron when expanded', () => {
    renderRow({}, { isExpanded: true })

    expect(screen.getByRole('button', { name: 'TP53 evidence review' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Hide transcript' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Expanded panel')).toBeInTheDocument()
  })

  it('marks a selected row with the checked checkbox', () => {
    renderRow({}, { isSelected: true })

    expect(screen.getByRole('checkbox', { name: 'Select TP53 evidence review' })).toBeChecked()
  })

  it('offers Rename, Copy session ID, and Delete from the overflow menu', async () => {
    const user = userEvent.setup()
    const handlers = renderRow()

    await user.click(screen.getByRole('button', { name: 'More actions for TP53 evidence review' }))
    const menu = screen.getByRole('menu', { name: 'Actions for TP53 evidence review' })
    expect(menu).toBeInTheDocument()

    await user.click(screen.getByRole('menuitem', { name: 'Rename' }))
    expect(handlers.onRename).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'More actions for TP53 evidence review' }))
    await user.click(screen.getByRole('menuitem', { name: 'Copy session ID' }))
    expect(handlers.onCopySessionId).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'More actions for TP53 evidence review' }))
    await user.click(screen.getByRole('menuitem', { name: 'Delete' }))
    expect(handlers.onDelete).toHaveBeenCalledTimes(1)
  })

  it('labels Agent Studio rows with the Studio tag and the studio restore action', () => {
    renderRow({ chat_kind: 'agent_studio' })

    expect(screen.getByText('Studio')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open in Agent Studio' })).toBeInTheDocument()
  })

  it('renders untitled sessions as Untitled conversation in italic secondary text', () => {
    renderRow({ title: null })

    const title = screen.getByText('Untitled conversation')
    expect(title).toHaveStyle({ fontStyle: 'italic' })
    expect(screen.getByRole('button', { name: 'Untitled conversation' })).toBeInTheDocument()
  })

  it('keeps the full long title available through the title attribute', () => {
    renderRow({ title: LONG_TITLE })

    const title = screen.getByText(LONG_TITLE)
    expect(title).toHaveAttribute('title', LONG_TITLE)
    expect(title).toHaveStyle({ textOverflow: 'ellipsis', whiteSpace: 'nowrap' })
  })

  it('reports an invalid activity timestamp as Unavailable', () => {
    renderRow({ recent_activity_at: 'not-a-date' })

    expect(screen.getByText('Unavailable')).toBeInTheDocument()
  })
})
