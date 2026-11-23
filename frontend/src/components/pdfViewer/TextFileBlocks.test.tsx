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

function renderSheet(
  block: DisplayBlock,
  highlightedBlocks: TextQuoteBlockSpan[] | null = null,
  highlightMode: 'hover' | 'select' = 'select',
) {
  return render(
    <TextFileBlocks
      blocks={[block]}
      highlightedBlocks={highlightedBlocks}
      highlightMode={highlightMode}
      sectionBlockIndex={null}
      firstHighlightRef={noRef}
    />,
  )
}

describe('TextFileBlocks: large tables in parts', () => {
  it('shows the first part of a long table, how many rows there are, and how to see more', () => {
    renderSheet(sheet(9799))

    const table = screen.getByRole('table')
    expect(renderedRows()).toHaveLength(TABLE_ROWS_PER_PART)
    expect(table).toHaveAttribute('aria-rowcount', '9800')
    expect(renderedRows()[499]).toHaveAttribute('aria-rowindex', '500')
    expect(screen.getByText(/^Showing rows 1 to 500 of 9,800\./)).toBeInTheDocument()
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
    // The rest is now one part, so a single "Show all" button remains; focus is on it.
    expect(screen.getByRole('button', { name: 'Show all 1,200 rows' })).toHaveFocus()
    expect(screen.getByText(/^Showing rows 1 to 1,000 of 1,200\./)).toBeInTheDocument()
  })

  it('shows every row on request, then no buttons, and moves focus to the count line, which says all are shown', async () => {
    const user = userEvent.setup()
    renderSheet(sheet(1199))

    await user.click(screen.getByRole('button', { name: 'Show all 1,200 rows' }))

    expect(renderedRows()).toHaveLength(1200)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Showing all 1,200 rows.')
    expect(status).toHaveFocus()
  })

  it('moves focus to the count line after the last "Show the next" too', async () => {
    const user = userEvent.setup()
    renderSheet(sheet(1199))

    await user.click(screen.getByRole('button', { name: 'Show the next 500 rows' }))
    // The rest (200 rows) is one part: one button, which shows all.
    await user.click(screen.getByRole('button', { name: 'Show all 1,200 rows' }))

    expect(screen.getByRole('status')).toHaveFocus()
  })

  it('announces a reveal politely, and its buttons read as written (not in capitals)', () => {
    renderSheet(sheet(9799))

    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByRole('status')).toHaveTextContent('Find in your browser searches only the rows shown.')
    expect(screen.getByRole('button', { name: 'Show the next 500 rows' })).toHaveStyle({ textTransform: 'none' })
  })

  it('never offers a tiny last part: a part takes the rest when fewer than 100 rows would be left', async () => {
    const user = userEvent.setup()
    renderSheet(sheet(1000))

    // 1,001 rows: after the first 500, the rest (501) is one part, so only "Show all" is offered.
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Show all 1,001 rows'])
    await user.click(screen.getByRole('button', { name: 'Show all 1,001 rows' }))
    expect(renderedRows()).toHaveLength(1001)
  })

  it('never offers fewer than 100 rows: a 1,100-row table ends on "Show the next 100 rows"', async () => {
    const user = userEvent.setup()
    renderSheet(sheet(1099))

    await user.click(screen.getByRole('button', { name: 'Show the next 500 rows' }))
    expect(screen.getByRole('button', { name: 'Show all 1,100 rows' })).toBeInTheDocument()
    expect(renderedRows()).toHaveLength(1000)
  })

  it('shows one button when the next part is the rest of the table', () => {
    renderSheet(sheet(600))

    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Show all 601 rows'])
  })

  it('shows a table of up to 600 rows whole', () => {
    renderSheet(sheet(599))

    expect(renderedRows()).toHaveLength(600)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('never reveals rows for a hover; only a click does', () => {
    renderSheet(sheet(9799), [{ blockIndex: 0, rowIndexes: [7000] }], 'hover')

    expect(renderedRows()).toHaveLength(500)
    expect(screen.getByRole('table').querySelector('tr[aria-current="true"]')).toBeNull()
  })

  it('highlights a hovered row that is already shown', () => {
    renderSheet(sheet(9799), [{ blockIndex: 0, rowIndexes: [10] }], 'hover')

    expect(screen.getByRole('table').querySelector('tr[aria-current="true"]')).toHaveTextContent('XS10')
  })

  it('renders the rows of a reveal once, not twice', () => {
    let cellReads = 0
    const block = sheet(9799)
    const counted: DisplayBlock = {
      ...block,
      rows: block.rows!.map((row) => ({
        line: row.line,
        is_header: row.is_header,
        get cells() { cellReads += 1; return row.cells },
      })),
    }
    renderSheet(counted, [{ blockIndex: 0, rowIndexes: [7000] }])

    expect(renderedRows()).toHaveLength(7500)
    // Each shown row is rendered once: keeping the reveal in state must not render them again.
    expect(cellReads).toBe(7500)
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
        <TextFileBlocks blocks={[block]} highlightedBlocks={null} highlightMode="select" sectionBlockIndex={null} firstHighlightRef={noRef} />,
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
