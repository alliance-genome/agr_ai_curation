/** Large tables shown in parts (ALL-1460, P9c). Blocks in the text route's shape (P9a). */
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TABLE_ROWS_PER_PART, TextFileBlocks } from './TextFileBlocks'
import type { DisplayBlock } from './viewerFiles'
import type { TextQuoteBlockSpan } from './viewerFileNavigation'

function sheet(dataRows: number): DisplayBlock {
  const header = { line: '| Strain | Temp |', cells: ['Strain', 'Temp'], is_header: true }
  const rows = [header, ...Array.from({ length: dataRows }, (_, index) => ({
    line: `| XS${index + 1} | 25 C |`,
    cells: [`XS${index + 1}`, '25 C'],
    is_header: false,
  }))]
  return {
    kind: 'table',
    text: [header.line, '|---|---|', ...rows.slice(1).map((row) => row.line)].join('\n'),
    level: null,
    section_title: 'Sheet 1',
    parent_section: null,
    subsection: null,
    section_path: ['Sheet 1'],
    rows,
  }
}

const noRef = () => undefined

/** Row counts by selector: role queries over thousands of rows are too slow in jsdom. */
function renderedRows(): HTMLTableRowElement[] {
  return Array.from(screen.getByRole('table').querySelectorAll('tr'))
}

function renderSheet(block: DisplayBlock, highlightedBlocks: TextQuoteBlockSpan[] | null = null) {
  return render(
    <TextFileBlocks blocks={[block]} highlightedBlocks={highlightedBlocks} sectionBlockIndex={null} firstHighlightRef={noRef} />,
  )
}

describe('TextFileBlocks: large tables in parts', () => {
  it('shows the first part of a long table, how many rows there are, and how to see more', () => {
    renderSheet(sheet(9799))

    const table = screen.getByRole('table')
    expect(renderedRows()).toHaveLength(TABLE_ROWS_PER_PART)
    expect(table).toHaveAttribute('aria-rowcount', '9800')
    expect(renderedRows()[499]).toHaveAttribute('aria-rowindex', '500')
    expect(screen.getByText('Showing rows 1 to 500 of 9,800.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show the next 500 rows' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show all 9,800 rows' })).toBeInTheDocument()
  })

  it('shows a table at or under one part whole, with no buttons', () => {
    renderSheet(sheet(399))

    const table = screen.getByRole('table')
    expect(renderedRows()).toHaveLength(400)
    expect(table).not.toHaveAttribute('aria-rowcount')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('adds the next part on request and keeps focus on the button', async () => {
    const user = userEvent.setup()
    renderSheet(sheet(1199))

    const next = screen.getByRole('button', { name: 'Show the next 500 rows' })
    await user.click(next)

    expect(renderedRows()).toHaveLength(1000)
    const nextAgain = screen.getByRole('button', { name: 'Show the next 200 rows' })
    expect(nextAgain).toBe(next)
    expect(nextAgain).toHaveFocus()
    expect(screen.getByText('Showing rows 1 to 1,000 of 1,200.')).toBeInTheDocument()
  })

  it('shows every row on request, then no buttons', async () => {
    const user = userEvent.setup()
    renderSheet(sheet(1199))

    await user.click(screen.getByRole('button', { name: 'Show all 1,200 rows' }))

    expect(renderedRows()).toHaveLength(1200)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('reveals the part holding highlighted evidence, and keeps it shown when the highlight moves on', () => {
    const block = sheet(9799)
    const view = renderSheet(block, [{ blockIndex: 0, rowIndexes: [7000] }])

    expect(renderedRows()).toHaveLength(7500)
    const highlighted = screen.getByRole('table').querySelector('tr[aria-current="true"]')
    expect(highlighted).toHaveTextContent('XS7000')
    expect(highlighted).toHaveAttribute('aria-rowindex', '7001')

    act(() => {
      view.rerender(
        <TextFileBlocks blocks={[block]} highlightedBlocks={null} sectionBlockIndex={null} firstHighlightRef={noRef} />,
      )
    })
    expect(renderedRows()).toHaveLength(7500)
  })

  it('reveals every part a two-row evidence spans', () => {
    renderSheet(sheet(1999), [{ blockIndex: 0, rowIndexes: [499, 500] }])

    expect(renderedRows()).toHaveLength(1000)
    expect(screen.getByRole('table').querySelectorAll('tr[aria-current="true"]')).toHaveLength(2)
  })
})
