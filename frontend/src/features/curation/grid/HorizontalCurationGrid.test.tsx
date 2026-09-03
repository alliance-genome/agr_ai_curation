import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import type { ComponentProps } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import theme, { createAppTheme } from '@/theme'
import type { FieldStateKind } from '@/features/curation/editor/fieldState'
import HorizontalCurationGrid from './HorizontalCurationGrid'
import {
  HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
  type HorizontalGridColumn,
  type HorizontalGridFieldCell,
  type HorizontalGridModel,
  type HorizontalGridRow,
  type HorizontalGridValidationProjection,
} from './horizontalGridModel'

const originalMatchMedia = window.matchMedia

const emptyValidation: HorizontalGridValidationProjection = {
  summaries: [],
  statuses: [],
  summaryCount: 0,
  findingCount: 0,
  openFindingCount: 0,
}

const columns: HorizontalGridColumn[] = [
  {
    key: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
    kind: 'context',
    fieldPath: null,
    label: 'Object',
    order: -1,
    required: false,
    readOnly: true,
    groupKey: null,
    groupLabel: null,
  },
  {
    key: 'field:alpha',
    kind: 'field',
    fieldPath: 'alpha',
    label: 'Alpha',
    order: 0,
    required: true,
    readOnly: false,
    groupKey: 'main',
    groupLabel: 'Main fields',
  },
  {
    key: 'field:beta',
    kind: 'field',
    fieldPath: 'beta',
    label: 'Beta',
    order: 1,
    required: false,
    readOnly: false,
    groupKey: 'main',
    groupLabel: 'Main fields',
  },
  {
    key: 'field:gamma',
    kind: 'field',
    fieldPath: 'gamma',
    label: 'Gamma',
    order: 2,
    required: false,
    readOnly: true,
    groupKey: null,
    groupLabel: null,
  },
]

function fieldCell(
  columnKey: string,
  fieldPath: string,
  value: unknown,
  hasField = true,
  state: FieldStateKind = 'ai-unconfirmed',
): HorizontalGridFieldCell {
  return {
    columnKey,
    fieldKey: hasField ? fieldPath : null,
    fieldPath,
    hasField,
    value,
    required: hasField ? false : null,
    readOnly: hasField ? false : null,
    staleValidation: hasField ? false : null,
    state: hasField ? state : null,
    fieldValidation: null,
    evidence: [],
    validation: emptyValidation,
    extractorComparison: null,
    valueSource: 'canonical',
  }
}

function row(candidateId = 'candidate-1'): HorizontalGridRow {
  return {
    candidateId,
    contextCell: {
      columnKey: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
      value: {
        candidateId,
        objectId: 'object-1',
        envelopeId: 'envelope-1',
        envelopeRevision: 1,
        objectType: 'DomainObject',
        objectRole: 'curatable_unit',
        identityLabel: 'Object one',
        secondaryLabel: 'Supporting context',
        candidateStatus: 'pending',
        candidateSource: 'extracted',
        candidateMetadata: {},
        summaryFields: [],
        reviewRowMetadata: {},
      },
      evidence: [],
      validation: emptyValidation,
    },
    cells: [
      fieldCell('field:alpha', 'alpha', 'Alpha value'),
      fieldCell('field:beta', 'beta', null, false),
      fieldCell('field:gamma', 'gamma', null),
    ],
    evidence: [],
    validation: emptyValidation,
    unmappedEvidence: [],
    unmappedValidation: emptyValidation,
  }
}

function model(rows: HorizontalGridRow[] = [row()]): HorizontalGridModel {
  return { columns, rows }
}

function renderGrid(
  gridModel = model(),
  props: Partial<ComponentProps<typeof HorizontalCurationGrid>> = {},
) {
  return render(
    <ThemeProvider theme={theme}>
      <HorizontalCurationGrid model={gridModel} {...props} />
    </ThemeProvider>,
  )
}

function displayedColumnKeys(): string[] {
  return within(screen.getByTestId('horizontal-grid-table'))
    .getAllByRole('columnheader')
    .map((header) => header.getAttribute('data-column-key') ?? 'actions')
}

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: originalMatchMedia,
  })
})

describe('HorizontalCurationGrid', () => {
  it('ports the prototype compact state surfaces and anchored action layout in light mode', () => {
    const lightModel = model([
      {
        ...row(),
        cells: [
          fieldCell('field:alpha', 'alpha', 'Alpha value', true, 'needs-review'),
          fieldCell('field:beta', 'beta', 'Beta value', true, 'ai-unconfirmed'),
          fieldCell('field:gamma', 'gamma', 'Gamma value', true, 'resolved'),
        ],
      },
      {
        ...row('candidate-2'),
        cells: [
          fieldCell('field:alpha', 'alpha', 'Alpha value two', true, 'resolved'),
          fieldCell('field:beta', 'beta', 'Beta value two', true, 'resolved'),
          fieldCell('field:gamma', 'gamma', 'Gamma value two', true, 'resolved'),
        ],
      },
    ])

    render(
      <ThemeProvider theme={createAppTheme('light')}>
        <HorizontalCurationGrid
          model={lightModel}
          renderCellActions={() => <button type="button">Cell action</button>}
        />
      </ThemeProvider>,
    )

    expect(document.querySelector('[data-field-state="needs-review"]')).toHaveStyle({
      backgroundColor: '#fffaf0',
      minHeight: '68px',
      padding: '6px 8px 30px 9px',
    })
    expect(document.querySelector('[data-field-state="ai-unconfirmed"]')).toHaveStyle({
      backgroundColor: '#fbe5e0',
    })
    const resolvedCells = document.querySelectorAll('[data-field-state="resolved"]')
    expect(getComputedStyle(resolvedCells[0]!).backgroundColor).toBe('rgba(0, 0, 0, 0)')
    expect(resolvedCells[0]?.closest('td')).toHaveStyle({ backgroundColor: '#ffffff' })
    expect(resolvedCells[1]?.closest('td')).toHaveStyle({ backgroundColor: '#fdfdfc' })
    expect(document.querySelector('[data-slot="cell-actions"]')).toHaveStyle({
      bottom: '4px',
      left: '7px',
      position: 'absolute',
    })
    expect(screen.getByRole('button', { name: 'Compact' })).toHaveStyle({
      backgroundColor: '#0b2f55',
      color: '#ffffff',
    })
    expect(screen.getByRole('button', { name: 'Object is always pinned' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('preserves alternating row depth beneath resolved cells in dark mode', () => {
    const resolvedModel = model([
      { ...row(), cells: row().cells.map((cell) => ({ ...cell, state: 'resolved' })) },
      {
        ...row('candidate-2'),
        cells: row('candidate-2').cells.map((cell) => ({
          ...cell,
          state: cell.hasField ? 'resolved' : null,
        })),
      },
    ])

    renderGrid(resolvedModel)
    const resolvedCells = document.querySelectorAll('[data-field-state="resolved"]')
    const firstRowSurface = getComputedStyle(resolvedCells[0]!.closest('td')!).backgroundColor
    const secondRowSurface = getComputedStyle(resolvedCells[3]!.closest('td')!).backgroundColor
    expect(firstRowSurface).not.toBe(secondRowSurface)
  })

  it('renders native table semantics, sticky edge columns, overflow access, and action slots', () => {
    renderGrid(model(), {
      renderCellActions: ({ column }) => <button type="button">Inspect {column.label}</button>,
      renderRowActions: (gridRow) => <button type="button">Review {gridRow.candidateId}</button>,
      selectedCandidateId: 'candidate-1',
    })

    expect(screen.getByRole('table', { name: 'Curation records arranged by field' })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader')).toHaveLength(5)
    expect(screen.getByRole('columnheader', { name: /Object/ })).toHaveAttribute('data-sticky', 'left')
    expect(screen.getByRole('columnheader', { name: 'Row actions' })).toHaveAttribute(
      'data-sticky',
      'right',
    )
    expect(
      screen.getByRole('region', { name: 'Horizontally scrollable curation grid' }),
    ).toHaveAttribute('tabindex', '0')
    expect(screen.getByTestId('horizontal-grid-scroll-region')).toHaveStyle({ overflow: 'auto' })
    expect(screen.getByTestId('horizontal-grid-table')).toHaveStyle({
      width: '876px',
      minWidth: '876px',
    })
    expect(screen.getByRole('button', { name: 'Inspect Alpha' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review candidate-1' })).toBeInTheDocument()
    expect(screen.getByTestId('horizontal-grid-selected-record')).toHaveTextContent('Object one selected')
    expect(screen.getByText('Pin headers')).toBeInTheDocument()
    expect(screen.getByText('Evidence')).toBeInTheDocument()
    expect(screen.getByText('Validate')).toBeInTheDocument()
    expect(screen.getByText('Edit')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Validation summary/ })).toBeInTheDocument()

    const dataRowElement = screen.getAllByRole('row')[1]
    const dataRow = within(dataRowElement)
    expect(dataRowElement).toHaveAttribute('data-selected', 'true')
    const dataCells = dataRow.getAllByRole('cell')
    expect(dataRow.getByRole('rowheader')).toHaveAttribute('data-sticky', 'left')
    expect(dataCells.at(-1)).toHaveAttribute('data-sticky', 'right')
  })

  it('keeps an overflowing row set inside the grid scroll region', () => {
    const overflowingRows = Array.from({ length: 30 }, (_, index) => row(`candidate-${index + 1}`))

    renderGrid(model(overflowingRows))

    expect(screen.getByTestId('horizontal-curation-grid')).toHaveStyle({
      flex: '1 1 0%',
      minHeight: '0',
      minWidth: '0',
      overflow: 'hidden',
    })
    expect(screen.getByTestId('horizontal-grid-scroll-region')).toHaveStyle({
      flex: '1 1 0%',
      minHeight: '0',
      overflow: 'auto',
    })
    expect(screen.getByTestId('horizontal-grid-table').querySelectorAll('tbody tr')).toHaveLength(30)
  })

  it('pins columns in interaction order and supports keyboard activation', async () => {
    const user = userEvent.setup()
    renderGrid()

    const betaPin = screen.getByRole('button', { name: 'Pin Beta column' })
    betaPin.focus()
    await user.keyboard('{Enter}')
    await user.click(screen.getByRole('button', { name: 'Pin Gamma column' }))

    expect(displayedColumnKeys()).toEqual([
      HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
      'field:beta',
      'field:gamma',
      'field:alpha',
      'actions',
    ])
    expect(screen.getByRole('button', { name: 'Unpin Beta column' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('status')).toHaveTextContent('Gamma column pinned beside Object')
  })

  it('filters stale pins when projection columns change', async () => {
    const user = userEvent.setup()
    const { rerender } = renderGrid()

    await user.click(screen.getByRole('button', { name: 'Pin Beta column' }))
    await user.click(screen.getByRole('button', { name: 'Pin Gamma column' }))

    const currentModel = model()
    const nextModel: HorizontalGridModel = {
      ...currentModel,
      columns: currentModel.columns.filter((column) => column.key !== 'field:beta'),
      rows: currentModel.rows.map((gridRow) => ({
        ...gridRow,
        cells: gridRow.cells.filter((cell) => cell.columnKey !== 'field:beta'),
      })),
    }
    rerender(
      <ThemeProvider theme={theme}>
        <HorizontalCurationGrid model={nextModel} />
      </ThemeProvider>,
    )

    expect(displayedColumnKeys()).toEqual([
      HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
      'field:gamma',
      'field:alpha',
      'actions',
    ])
    expect(screen.getByRole('columnheader', { name: /Gamma/ })).toHaveStyle({ left: '220px' })
    expect(screen.getByRole('button', { name: 'Clear 1 optional pinned column' })).toBeEnabled()
  })

  it('clears only optional pins and keeps the identity column locked', async () => {
    const user = userEvent.setup()
    renderGrid()

    await user.click(screen.getByRole('button', { name: 'Pin Alpha column' }))
    await user.click(screen.getByRole('button', { name: 'Pin Beta column' }))
    await user.click(screen.getByRole('button', { name: 'Clear 2 optional pinned columns' }))

    expect(displayedColumnKeys()).toEqual([
      HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
      'field:alpha',
      'field:beta',
      'field:gamma',
      'actions',
    ])
    expect(screen.getByLabelText('Object is always pinned')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'No optional pinned columns to clear' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent(
      '2 optional pinned columns cleared; Object remains pinned',
    )
  })

  it('switches between compact and comfortable feature-local density', async () => {
    const user = userEvent.setup()
    renderGrid()

    const root = screen.getByTestId('horizontal-curation-grid')
    const compact = screen.getByRole('button', { name: 'Compact' })
    const comfortable = screen.getByRole('button', { name: 'Comfortable' })

    expect(root).toHaveAttribute('data-density', 'compact')
    expect(compact).toHaveAttribute('aria-pressed', 'true')
    expect(comfortable).toHaveAttribute('aria-pressed', 'false')

    await user.click(comfortable)

    expect(root).toHaveAttribute('data-density', 'comfortable')
    expect(compact).toHaveAttribute('aria-pressed', 'false')
    expect(comfortable).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Comfortable row density enabled')
  })

  it('supports arrow-key and Shift-plus-wheel horizontal navigation', () => {
    renderGrid()
    const scrollRegion = screen.getByTestId('horizontal-grid-scroll-region')

    fireEvent.keyDown(scrollRegion, { key: 'ArrowRight' })
    expect(scrollRegion.scrollLeft).toBe(184)

    const wheelEvent = new WheelEvent('wheel', {
      cancelable: true,
      deltaX: 0,
      deltaY: 60,
      shiftKey: true,
    })
    expect(scrollRegion.dispatchEvent(wheelEvent)).toBe(false)
    expect(wheelEvent.defaultPrevented).toBe(true)
    expect(scrollRegion.scrollLeft).toBe(244)

    fireEvent.keyDown(scrollRegion, { key: 'ArrowLeft' })
    expect(scrollRegion.scrollLeft).toBe(60)
  })

  it('uses reduced-motion presentation when the user requests it', () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query === '(prefers-reduced-motion: reduce)',
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })

    renderGrid()

    expect(screen.getByTestId('horizontal-curation-grid')).toHaveAttribute(
      'data-reduced-motion',
      'true',
    )
    expect(screen.getByTestId('horizontal-grid-scroll-region')).toHaveStyle({ scrollBehavior: 'auto' })
  })

  it('distinguishes unavailable and empty cells and preserves an empty table shell', () => {
    const { rerender } = renderGrid()

    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(screen.getByLabelText('Empty value')).toHaveTextContent('—')

    rerender(
      <ThemeProvider theme={theme}>
        <HorizontalCurationGrid model={model([])} />
      </ThemeProvider>,
    )

    expect(screen.getByText('No curation rows')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})
