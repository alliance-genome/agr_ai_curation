import { Fragment, memo, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Box, Button, Stack, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { TextQuoteBlockSpan } from './viewerFileNavigation'
import type { DisplayBlock } from './viewerFiles'

/**
 * Rendering of a text file's display blocks (ALL-1460, P9b and P9c).
 *
 * Every block renders to the DOM. A table longer than `TABLE_ROWS_PER_PART` shows its
 * rows in parts (P9c): the first part, then more on request; evidence on a later row
 * reveals the parts up to it. Every shown row is real DOM, so the browser's find,
 * copy and screen readers work on what is shown.
 */

/** Rows per part of a large table (a rendering constant; about 0.1 s for 500 rows of 8 cells in jsdom). */
export const TABLE_ROWS_PER_PART = 500

/** Proposals, awaiting sign-off. */
function shownRowsText(shown: number, total: number): string {
  return `Showing rows 1 to ${shown.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}.`
}
function showNextRowsText(count: number): string {
  return `Show the next ${count.toLocaleString('en-US')} rows`
}
function showAllRowsText(total: number): string {
  return `Show all ${total.toLocaleString('en-US')} rows`
}

/** How many rows to render: a whole part holding every highlighted row, and at least what was shown. */
function rowsToRender(total: number, shown: number, highlightedRowIndexes: readonly number[]): number {
  if (total <= TABLE_ROWS_PER_PART) {
    return total
  }
  const lastHighlighted = highlightedRowIndexes.length > 0 ? Math.max(...highlightedRowIndexes) : -1
  const neededForHighlight = (Math.floor(lastHighlighted / TABLE_ROWS_PER_PART) + 1) * TABLE_ROWS_PER_PART
  return Math.min(total, Math.max(shown, lastHighlighted >= 0 ? neededForHighlight : 0))
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
  /** Receives the first highlighted row, so the view can scroll to it. */
  firstHighlightRef: ((element: HTMLElement | null) => void) | undefined
  highlightStyle: HighlightStyle
}

const NO_ROWS: readonly number[] = []

/** Memoised: a large sheet renders again only when its rows or its highlight change. */
export const TextTableBlock = memo(function TextTableBlock({
  block,
  highlightedRowIndexes,
  firstHighlightRef,
  highlightStyle,
}: TextTableBlockProps) {
  const rows = block.rows ?? []
  const [shown, setShown] = useState(TABLE_ROWS_PER_PART)
  const visible = rowsToRender(rows.length, shown, highlightedRowIndexes)
  // Rows revealed for evidence stay shown when the highlight moves on.
  useEffect(() => {
    if (visible > shown) {
      setShown(visible)
    }
  }, [shown, visible])
  const inParts = rows.length > TABLE_ROWS_PER_PART
  const remaining = rows.length - visible
  const firstHighlighted = highlightedRowIndexes[0]
  return (
    <>
      <table aria-rowcount={inParts ? rows.length : undefined}>
        <tbody>
          {rows.slice(0, visible).map((row, rowIndex) => {
            const highlighted = highlightedRowIndexes.includes(rowIndex)
            return (
              <Box
                component="tr"
                key={rowIndex}
                ref={rowIndex === firstHighlighted ? firstHighlightRef : undefined}
                aria-current={highlighted ? true : undefined}
                aria-rowindex={inParts ? rowIndex + 1 : undefined}
                sx={highlighted ? highlightStyle : undefined}
              >
                {row.cells.map((cell, cellIndex) => (
                  <Fragment key={cellIndex}>
                    {row.is_header ? <th scope="col">{cell}</th> : <td>{cell}</td>}
                  </Fragment>
                ))}
              </Box>
            )
          })}
        </tbody>
      </table>
      {inParts && remaining > 0 ? (
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 1.5 }}>
          <Typography variant="body2" color="text.secondary">{shownRowsText(visible, rows.length)}</Typography>
          <Button size="small" variant="outlined" onClick={() => setShown(visible + TABLE_ROWS_PER_PART)}>
            {showNextRowsText(Math.min(TABLE_ROWS_PER_PART, remaining))}
          </Button>
          <Button size="small" onClick={() => setShown(rows.length)}>
            {showAllRowsText(rows.length)}
          </Button>
        </Stack>
      ) : null}
    </>
  )
})

export interface TextFileBlocksProps {
  blocks: readonly DisplayBlock[]
  /** The blocks (and rows) the located evidence covers; null when none. */
  highlightedBlocks: readonly TextQuoteBlockSpan[] | null
  /** The block shown as the evidence's section when its quote was not found; null otherwise. */
  sectionBlockIndex: number | null
  /** Receives the first highlighted block or row. */
  firstHighlightRef: (element: HTMLElement | null) => void
}

/** Memoised: a viewer state change unrelated to the text does not render the blocks again. */
export const TextFileBlocks = memo(function TextFileBlocks({
  blocks,
  highlightedBlocks,
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
