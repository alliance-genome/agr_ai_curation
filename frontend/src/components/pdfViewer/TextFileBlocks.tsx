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
  /** Receives the first highlighted block or row. */
  firstHighlightRef: (element: HTMLElement | null) => void
}

export function TextFileBlocks({ blocks, location, firstHighlightRef }: TextFileBlocksProps) {
  const highlightStyle = useHighlightStyle()
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

  blocks.forEach((block, blockIndex) => {
    const span = spans.get(blockIndex)
    const isFirst = blockIndex === firstBlockIndex
    if (block.kind === 'table') {
      flushList(`list-${blockIndex}`)
      nodes.push(
        <TextTableBlock
          key={blockIndex}
          block={block}
          highlightedRowIndexes={span?.rowIndexes ?? []}
          firstHighlightRef={isFirst ? firstHighlightRef : undefined}
          highlightStyle={highlightStyle}
        />,
      )
      return
    }
    const highlightProps = span
      ? { ref: isFirst ? firstHighlightRef : undefined, 'aria-current': true as const, sx: highlightStyle }
      : {}
    switch (block.kind) {
      case 'list_item':
        listItems.push(<Box component="li" key={blockIndex} {...highlightProps}>{block.text}</Box>)
        return
      case 'heading': {
        flushList(`list-${blockIndex}`)
        const level = Math.min((block.level ?? 1) + 2, 6)
        nodes.push(
          <Typography
            key={blockIndex}
            component={`h${level}` as 'h3'}
            variant="subtitle1"
            ref={span && isFirst ? firstHighlightRef : undefined}
            aria-current={span ? true : undefined}
            sx={{ fontWeight: 700, mt: 1.5, ...(span ? highlightStyle : {}) }}
          >
            {block.text}
          </Typography>,
        )
        return
      }
      case 'paragraph':
        flushList(`list-${blockIndex}`)
        nodes.push(<Box component="p" key={blockIndex} {...highlightProps}>{block.text}</Box>)
        return
      case 'code':
        flushList(`list-${blockIndex}`)
        nodes.push(<Box component="pre" key={blockIndex} {...highlightProps}><code>{block.text}</code></Box>)
        return
    }
  })
  flushList('list-end')
  return <>{nodes}</>
}
