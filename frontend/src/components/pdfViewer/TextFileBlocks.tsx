import { Fragment, type ReactNode } from 'react'
import { Box, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { TextQuoteLocation } from './viewerFileNavigation'
import type { DisplayBlock } from './viewerFiles'

/**
 * Plain rendering of a text file's display blocks (ALL-1460, P9b).
 *
 * Every block and every table row renders to the DOM. P9c replaces `TextTableBlock`'s
 * body with a windowed one for large sheets; its props (the rows, the rows to
 * highlight, a ref for the first highlighted row) are the seam and stay as they are.
 */

export interface HighlightStyle {
  backgroundColor: string
  outline: string
}

export function useHighlightStyle(): HighlightStyle {
  const theme = useTheme()
  return {
    backgroundColor: alpha(theme.palette.success.main, 0.22),
    outline: `2px solid ${alpha(theme.palette.success.main, 0.9)}`,
  }
}

export interface TextTableBlockProps {
  block: DisplayBlock
  /** Rows the located quote covers; empty when none. */
  highlightedRowIndexes: readonly number[]
  /** Receives the first highlighted row, so the view can scroll to it. */
  firstHighlightRef: ((element: HTMLElement | null) => void) | undefined
  highlightStyle: HighlightStyle
}

export function TextTableBlock({ block, highlightedRowIndexes, firstHighlightRef, highlightStyle }: TextTableBlockProps) {
  const firstHighlighted = highlightedRowIndexes[0]
  return (
    <table>
      <tbody>
        {(block.rows ?? []).map((row, rowIndex) => {
          const highlighted = highlightedRowIndexes.includes(rowIndex)
          return (
            <Box
              component="tr"
              key={rowIndex}
              ref={rowIndex === firstHighlighted ? firstHighlightRef : undefined}
              aria-current={highlighted ? true : undefined}
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
  )
}

export interface TextFileBlocksProps {
  blocks: readonly DisplayBlock[]
  location: TextQuoteLocation | null
  /** The block shown as the evidence's section when its quote was not found; null otherwise. */
  sectionBlockIndex: number | null
  /** Receives the first highlighted block or row. */
  firstHighlightRef: (element: HTMLElement | null) => void
}

export function TextFileBlocks({ blocks, location, sectionBlockIndex, firstHighlightRef }: TextFileBlocksProps) {
  const highlightStyle = useHighlightStyle()
  const theme = useTheme()
  const spans = new Map((location?.blocks ?? []).map((span) => [span.blockIndex, span]))
  const firstBlockIndex = location?.blocks[0]?.blockIndex

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
              highlightedRowIndexes={span?.rowIndexes ?? []}
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
}
