import { Fragment, memo, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Box, Button, Stack, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { TextQuoteBlockSpan } from './viewerFileNavigation'
import type { DisplayBlock, DisplayTableRow } from './viewerFiles'

/**
 * Rendering of a text file's display blocks (ALL-1460, P9b and P9c).
 *
 * Every block renders to the DOM. A table longer than `TABLE_ROWS_PER_PART` shows its
 * rows in parts (P9c): the first part, then more on request; evidence on a later row
 * reveals the parts up to it. Every shown row is real DOM, so the browser's find,
 * copy and screen readers work on what is shown.
 */

/** Rows per part of a large table (a rendering constant; about 0.1 s for 500 rows of 8 cells). */
export const TABLE_ROWS_PER_PART = 500
/**
 * A part takes the rest of the table when fewer rows than this would be left, so a
 * "Show the next" button never offers fewer than 100 rows; a table of up to 600 rows
 * renders whole. Row counts include the header row, as a spreadsheet numbers it.
 */
export const TABLE_PART_MIN_REMAINDER = 100

/** Proposals, awaiting sign-off. */
function shownRowsText(shown: number, total: number): string {
  return `Showing rows 1 to ${shown.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}. Find in your browser searches only the rows shown.`
}
function allRowsShownText(total: number): string {
  return `Showing all ${total.toLocaleString('en-US')} rows.`
}
function showNextRowsText(count: number): string {
  return `Show the next ${count.toLocaleString('en-US')} rows`
}
function showAllRowsText(total: number): string {
  return `Show all ${total.toLocaleString('en-US')} rows`
}

function rendersWhole(total: number): boolean {
  return total <= TABLE_ROWS_PER_PART + TABLE_PART_MIN_REMAINDER
}

/** Where the part starting at `start` ends: a whole part, or the rest when little would be left. */
function partEnd(total: number, start: number): number {
  const end = start + TABLE_ROWS_PER_PART
  return total - end < TABLE_PART_MIN_REMAINDER ? total : end
}

/** How many rows to render: what was shown, extended by whole parts until every revealed row is in. */
function rowsToRender(total: number, shown: number, revealRowIndexes: readonly number[]): number {
  if (rendersWhole(total)) {
    return total
  }
  const lastRevealed = revealRowIndexes.length > 0 ? Math.max(...revealRowIndexes) : -1
  let visible = Math.min(total, shown)
  while (visible <= lastRevealed && visible < total) {
    visible = partEnd(total, visible)
  }
  return visible
}

export interface HighlightStyle {
  backgroundColor: string
  outline: string
}

export function useHighlightStyle(): HighlightStyle {
  const theme = useTheme()
  return useMemo(() => ({
    backgroundColor: alpha(theme.palette.success.main, 0.22),
    outline: `2px solid ${alpha(theme.palette.success.main, 0.9)}`,
  }), [theme])
}

export interface TextTableBlockProps {
  block: DisplayBlock
  /** Rows the located quote covers; empty when none. */
  highlightedRowIndexes: readonly number[]
  /** A click reveals hidden parts to show its rows; a hover never does. */
  revealForHighlight: boolean
  /** Receives the first highlighted row, so the view can scroll to it. */
  firstHighlightRef: ((element: HTMLElement | null) => void) | undefined
  highlightStyle: HighlightStyle
}

const NO_ROWS: readonly number[] = []

interface TablePartProps {
  rows: readonly DisplayTableRow[]
  start: number
  end: number
  inParts: boolean
  /** Highlighted row indexes inside this part only. */
  highlighted: readonly number[]
  firstHighlighted: number | undefined
  firstHighlightRef: ((element: HTMLElement | null) => void) | undefined
  highlightStyle: HighlightStyle
}

function samePart(previous: TablePartProps, next: TablePartProps): boolean {
  return previous.rows === next.rows
    && previous.start === next.start
    && previous.end === next.end
    && previous.inParts === next.inParts
    && previous.firstHighlighted === next.firstHighlighted
    && previous.firstHighlightRef === next.firstHighlightRef
    && previous.highlightStyle === next.highlightStyle
    && previous.highlighted.length === next.highlighted.length
    && previous.highlighted.every((index, position) => index === next.highlighted[position])
}

/**
 * One part of a table in its own `<tbody>`, memoised: revealing more rows adds new
 * parts and never re-renders or re-inserts the shown ones (one insert per part, not
 * one per row after the existing rows).
 */
const TablePart = memo(function TablePart({
  rows,
  start,
  end,
  inParts,
  highlighted,
  firstHighlighted,
  firstHighlightRef,
  highlightStyle,
}: TablePartProps) {
  const cellsOf: ReactNode[] = []
  for (let rowIndex = start; rowIndex < end; rowIndex += 1) {
    const row = rows[rowIndex]!
    const isHighlighted = highlighted.includes(rowIndex)
    cellsOf.push(
      <tr
        key={rowIndex}
        ref={rowIndex === firstHighlighted ? firstHighlightRef : undefined}
        aria-current={isHighlighted ? true : undefined}
        aria-rowindex={inParts ? rowIndex + 1 : undefined}
        style={isHighlighted ? highlightStyle : undefined}
      >
        {row.cells.map((cell, cellIndex) => (
          <Fragment key={cellIndex}>
            {row.is_header ? <th scope="col">{cell}</th> : <td>{cell}</td>}
          </Fragment>
        ))}
      </tr>,
    )
  }
  return <tbody>{cellsOf}</tbody>
}, samePart)

/** Memoised: a large sheet renders again only when its rows, its highlight or its parts change. */
export const TextTableBlock = memo(function TextTableBlock({
  block,
  highlightedRowIndexes,
  revealForHighlight,
  firstHighlightRef,
  highlightStyle,
}: TextTableBlockProps) {
  const rows = block.rows ?? []
  const total = rows.length
  const [shown, setShown] = useState(() => partEnd(total, 0))
  const visible = rowsToRender(total, shown, revealForHighlight ? highlightedRowIndexes : NO_ROWS)
  // Rows revealed for evidence stay shown when the highlight moves on. Set during
  // render (state derived from props), so a reveal renders its rows once.
  if (visible > shown) {
    setShown(visible)
  }
  const inParts = !rendersWhole(total)
  const remaining = total - visible
  const statusRef = useRef<HTMLDivElement | null>(null)
  const focusStatusAfterRevealRef = useRef(false)
  useEffect(() => {
    if (focusStatusAfterRevealRef.current && remaining === 0) {
      focusStatusAfterRevealRef.current = false
      statusRef.current?.focus()
    }
  }, [remaining])

  const parts: Array<{ start: number; end: number }> = []
  for (let start = 0; start < visible; start = partEnd(total, start)) {
    parts.push({ start, end: Math.min(visible, partEnd(total, start)) })
  }
  const firstHighlighted = highlightedRowIndexes.find((index) => index < visible)
  const nextEnd = partEnd(total, visible)

  return (
    <>
      <table aria-rowcount={inParts ? total : undefined}>
        {parts.map(({ start, end }) => (
          <TablePart
            key={start}
            rows={rows}
            start={start}
            end={end}
            inParts={inParts}
            highlighted={highlightedRowIndexes.filter((index) => index >= start && index < end)}
            firstHighlighted={firstHighlighted !== undefined && firstHighlighted >= start && firstHighlighted < end ? firstHighlighted : undefined}
            firstHighlightRef={firstHighlightRef}
            highlightStyle={highlightStyle}
          />
        ))}
      </table>
      {inParts ? (
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 1.5 }}>
          <Typography
            ref={statusRef}
            role="status"
            aria-live="polite"
            tabIndex={-1}
            variant="body2"
            color="text.secondary"
          >
            {remaining > 0 ? shownRowsText(visible, total) : allRowsShownText(total)}
          </Typography>
          {remaining > 0 ? (
            <>
              {/*
                When the next part is the rest of the table, one button shows it all. It is
                the same element as "Show the next", so focus stays on it after a part.
              */}
              <Button
                size="small"
                variant="outlined"
                sx={{ textTransform: 'none' }}
                onClick={() => {
                  focusStatusAfterRevealRef.current = nextEnd === total
                  setShown(nextEnd)
                }}
              >
                {nextEnd === total ? showAllRowsText(total) : showNextRowsText(nextEnd - visible)}
              </Button>
              {nextEnd < total ? (
                <Button
                  size="small"
                  sx={{ textTransform: 'none' }}
                  onClick={() => {
                    focusStatusAfterRevealRef.current = true
                    setShown(total)
                  }}
                >
                  {showAllRowsText(total)}
                </Button>
              ) : null}
            </>
          ) : null}
        </Stack>
      ) : null}
    </>
  )
})

export interface TextFileBlocksProps {
  blocks: readonly DisplayBlock[]
  /** The blocks (and rows) the located evidence covers; null when none. */
  highlightedBlocks: readonly TextQuoteBlockSpan[] | null
  /** A click ('select') reveals hidden rows of a long table; a hover never does. */
  highlightMode: 'hover' | 'select'
  /** The block shown as the evidence's section when its quote was not found; null otherwise. */
  sectionBlockIndex: number | null
  /** Receives the first highlighted block or row. */
  firstHighlightRef: (element: HTMLElement | null) => void
}

/** Memoised: a viewer state change unrelated to the text does not render the blocks again. */
export const TextFileBlocks = memo(function TextFileBlocks({
  blocks,
  highlightedBlocks,
  highlightMode,
  sectionBlockIndex,
  firstHighlightRef,
}: TextFileBlocksProps) {
  const highlightStyle = useHighlightStyle()
  const theme = useTheme()
  const spans = new Map((highlightedBlocks ?? []).map((span) => [span.blockIndex, span]))
  const firstBlockIndex = highlightedBlocks?.[0]?.blockIndex

  const nodes: ReactNode[] = []
  let listItems: ReactNode[] = []
  const flushList = (key: string) => {
    if (listItems.length > 0) {
      nodes.push(<ul key={key}>{listItems}</ul>)
      listItems = []
    }
  }
  // The evidence's section, shown when its quote was not found: marked and scrolled
  // to, without a quote highlight.
  const asSection = (blockIndex: number, content: ReactNode): ReactNode => (
    blockIndex === sectionBlockIndex
      ? (
          <Box
            ref={firstHighlightRef}
            data-highlight-kind="section"
            aria-current="location"
            sx={{ outline: `2px dashed ${alpha(theme.palette.text.secondary, 0.7)}`, outlineOffset: 2 }}
          >
            {content}
          </Box>
        )
      : content
  )

  blocks.forEach((block, blockIndex) => {
    const span = spans.get(blockIndex)
    const isFirst = blockIndex === firstBlockIndex
    if (block.kind === 'table') {
      flushList(`list-${blockIndex}`)
      nodes.push(
        <Fragment key={blockIndex}>
          {asSection(blockIndex, (
            <TextTableBlock
              block={block}
              highlightedRowIndexes={span?.rowIndexes ?? NO_ROWS}
              revealForHighlight={highlightMode === 'select'}
              firstHighlightRef={isFirst ? firstHighlightRef : undefined}
              highlightStyle={highlightStyle}
            />
          ))}
        </Fragment>,
      )
      return
    }
    const highlightProps = span
      ? { ref: isFirst ? firstHighlightRef : undefined, 'aria-current': true as const, sx: highlightStyle }
      : {}
    switch (block.kind) {
      case 'list_item':
        listItems.push(
          <Box component="li" key={blockIndex} {...highlightProps}>{asSection(blockIndex, block.text)}</Box>,
        )
        return
      case 'heading': {
        flushList(`list-${blockIndex}`)
        const level = Math.min((block.level ?? 1) + 2, 6)
        nodes.push(
          <Fragment key={blockIndex}>
            {asSection(blockIndex, (
              <Typography
                component={`h${level}` as 'h3'}
                variant="subtitle1"
                ref={span && isFirst ? firstHighlightRef : undefined}
                aria-current={span ? true : undefined}
                sx={{ fontWeight: 700, mt: 1.5, ...(span ? highlightStyle : {}) }}
              >
                {block.text}
              </Typography>
            ))}
          </Fragment>,
        )
        return
      }
      case 'paragraph':
        flushList(`list-${blockIndex}`)
        nodes.push(
          <Fragment key={blockIndex}>
            {asSection(blockIndex, <Box component="p" {...highlightProps}>{block.text}</Box>)}
          </Fragment>,
        )
        return
      case 'code':
        flushList(`list-${blockIndex}`)
        nodes.push(
          <Fragment key={blockIndex}>
            {asSection(blockIndex, <Box component="pre" {...highlightProps}><code>{block.text}</code></Box>)}
          </Fragment>,
        )
        return
    }
  })
  flushList('list-end')
  return <>{nodes}</>
})
