import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AgentToolsTable from './AgentToolsTable'

const SEVEN_TOOLS = ['lookup_term', 'search_synonyms', 'get_id', 'search_db', 'record_evidence', 'lookup_condition', 'finalize']

describe('AgentToolsTable', () => {
  it('shows a count, three preview chips, a remainder, and a closed disclosure for many tools', () => {
    render(<AgentToolsTable tools={SEVEN_TOOLS} descriptions={{}} onShowDetails={vi.fn()} />)

    expect(screen.getByText('7 tools')).toBeInTheDocument()
    expect(screen.getByText('lookup_term')).toBeInTheDocument()
    expect(screen.getByText('search_synonyms')).toBeInTheDocument()
    expect(screen.getByText('get_id')).toBeInTheDocument()
    expect(screen.queryByText('search_db')).not.toBeInTheDocument()
    expect(screen.getByText('+4')).toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: 'Show all tools' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('opens into a table with purpose text and a Details link per tool', () => {
    const onShowDetails = vi.fn()
    render(
      <AgentToolsTable
        tools={SEVEN_TOOLS}
        descriptions={{ lookup_term: 'Find a term by name.' }}
        onShowDetails={onShowDetails}
      />
    )

    const toggle = screen.getByRole('button', { name: 'Show all tools' })
    fireEvent.click(toggle)

    const hide = screen.getByRole('button', { name: 'Hide tools' })
    expect(hide).toHaveAttribute('aria-expanded', 'true')

    const table = screen.getByRole('table', { name: 'Tools' })
    expect(within(table).getAllByRole('row')).toHaveLength(SEVEN_TOOLS.length + 1)
    expect(within(table).getByText('Find a term by name.')).toBeInTheDocument()
    expect(within(table).getAllByText('No description yet')).toHaveLength(SEVEN_TOOLS.length - 1)
    expect(screen.queryByText('+4')).not.toBeInTheDocument()

    fireEvent.click(within(table).getByRole('button', { name: 'Details for search_db' }))
    expect(onShowDetails).toHaveBeenCalledWith('search_db')

    fireEvent.click(hide)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show all tools' })).toBeInTheDocument()
  })

  it('handles few tools without a remainder', () => {
    render(<AgentToolsTable tools={['format_tsv', 'list_fields']} descriptions={{}} onShowDetails={vi.fn()} />)
    expect(screen.getByText('2 tools')).toBeInTheDocument()
    expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument()
  })

  it('states when the agent has no tools', () => {
    render(<AgentToolsTable tools={[]} descriptions={{}} onShowDetails={vi.fn()} />)
    expect(screen.getByText(/This agent has no tools/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show all tools' })).not.toBeInTheDocument()
  })

  it('exposes long tool names through a title attribute', () => {
    const longName = 'an_extremely_long_tool_name_that_would_otherwise_break_the_table_layout_when_rendered'
    render(<AgentToolsTable tools={[longName]} descriptions={{}} onShowDetails={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show all tools' }))
    expect(screen.getByTitle(longName)).toBeInTheDocument()
  })
})
