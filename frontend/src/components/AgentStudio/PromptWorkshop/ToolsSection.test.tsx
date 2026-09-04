import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ToolIdeaRequest, ToolLibraryItem } from '@/types/promptExplorer'

import ToolsSection, { toolPolicyBadge, type ToolsSectionProps } from './ToolsSection'

const toolLibrary: ToolLibraryItem[] = [
  {
    tool_key: 'search_document',
    display_name: 'Search Document',
    description: 'Search document sections',
    category: 'Document',
    curator_visible: true,
    allow_attach: true,
    allow_execute: true,
    config: { requires_document: true },
  },
  {
    tool_key: 'chebi_lookup',
    display_name: 'ChEBI Lookup',
    description: 'Chemicals',
    category: 'External API',
    curator_visible: true,
    allow_attach: true,
    allow_execute: true,
    config: { requires_document: false },
  },
  {
    tool_key: 'blocked_tool',
    display_name: 'Blocked Tool',
    description: 'Cannot run for custom agents',
    category: 'Admin',
    curator_visible: true,
    allow_attach: true,
    allow_execute: false,
    config: { requires_document: false },
  },
]

function buildRequest(overrides: Partial<ToolIdeaRequest>): ToolIdeaRequest {
  return {
    id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    user_id: 1,
    title: 'GO synonym expansion',
    description: 'desc',
    opus_conversation: [],
    status: 'in_progress',
    created_at: '2026-08-28T10:00:00Z',
    updated_at: '2026-08-28T10:00:00Z',
    ...overrides,
  }
}

function renderTools(overrides: Partial<ToolsSectionProps> = {}) {
  const props: ToolsSectionProps = {
    selectedToolIds: ['search_document', 'chebi_lookup', 'blocked_tool'],
    toolLibrary,
    onRemoveTool: vi.fn(),
    onAddTools: vi.fn(),
    hasTemplate: true,
    requests: [],
    requestsLoading: false,
    onNewRequest: vi.fn(),
    onAskClaudeToDraft: vi.fn(),
    ...overrides,
  }
  render(<ToolsSection {...props} />)
  return props
}

describe('ToolsSection', () => {
  it('renders attached tools as a table with purpose, policy badges, and remove buttons', () => {
    const props = renderTools()
    const table = screen.getByRole('table', { name: 'Attached tools' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('search_document')
    expect(rows[0]).toHaveTextContent('Search document sections')
    expect(rows[0]).toHaveTextContent('needs document')
    expect(rows[2]).toHaveTextContent('disabled by policy')
    expect(rows[1]).not.toHaveTextContent('needs document')

    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Remove chebi_lookup' }))
    expect(props.onRemoveTool).toHaveBeenCalledWith('chebi_lookup')
  })

  it('shows an empty state with zero tools and opens the library', () => {
    const props = renderTools({ selectedToolIds: [] })
    expect(screen.getByText(/No tools attached/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add tools' }))
    expect(props.onAddTools).toHaveBeenCalledTimes(1)
  })

  it('lists requests to developers with sent date, id, and status', () => {
    renderTools({
      requests: [
        buildRequest({}),
        buildRequest({ id: 'ffffffff-1111-2222-3333-444444444444', title: 'Bulk DOID lookup', status: 'completed', resulting_tool_key: 'bulk_doid' }),
        buildRequest({ id: '99999999-1111-2222-3333-444444444444', title: 'Fresh idea', status: 'submitted' }),
      ],
    })
    const list = screen.getByRole('list', { name: 'Requests to developers' })
    const items = within(list).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('GO synonym expansion')
    expect(items[0]).toHaveTextContent('request aaaaaa')
    expect(items[0]).toHaveTextContent('In progress')
    expect(items[1]).toHaveTextContent('Shipped bulk_doid')
    expect(items[2]).toHaveTextContent('New')
  })

  it('offers a new request and an AI Chat draft link', () => {
    const props = renderTools()
    expect(screen.getByText('No requests sent yet.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'New request' }))
    expect(props.onNewRequest).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Ask AI Chat to draft a request' }))
    expect(props.onAskClaudeToDraft).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Manage Tools' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send to Developers' })).not.toBeInTheDocument()
  })

  it('derives policy badges from the library entry', () => {
    expect(toolPolicyBadge(undefined)).toBeNull()
    expect(toolPolicyBadge(toolLibrary[0])).toBe('needs document')
    expect(toolPolicyBadge(toolLibrary[1])).toBeNull()
    expect(toolPolicyBadge(toolLibrary[2])).toBe('disabled by policy')
  })
})
