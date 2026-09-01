import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import ClearAllRoundedIcon from '@mui/icons-material/ClearAllRounded'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import FindInPageOutlinedIcon from '@mui/icons-material/FindInPageOutlined'
import PushPinOutlinedIcon from '@mui/icons-material/PushPinOutlined'
import PushPinRoundedIcon from '@mui/icons-material/PushPinRounded'
import {
  Box,
  Button,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { alpha, useMediaQuery, useTheme } from '@mui/material'

import {
  HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
  type HorizontalGridColumn,
  type HorizontalGridContextCell,
  type HorizontalGridFieldCell,
  type HorizontalGridModel,
  type HorizontalGridRow,
} from './horizontalGridModel'
import { formatHorizontalGridValue } from './horizontalGridFormatting'

const CONTEXT_COLUMN_WIDTH = 220
const FIELD_COLUMN_WIDTH = 184
const ACTION_COLUMN_WIDTH = 104

export type HorizontalGridDensity = 'compact' | 'comfortable'

export interface HorizontalGridContextRenderArgs {
  cell: HorizontalGridContextCell
  row: HorizontalGridRow
}

export interface HorizontalGridFieldRenderArgs {
  cell: HorizontalGridFieldCell
  column: HorizontalGridColumn
  row: HorizontalGridRow
}

export interface HorizontalCurationGridProps {
  model: HorizontalGridModel
  caption?: string
  accessibleLabel?: string
  rowActionsLabel?: string
  renderContextCell?: (args: HorizontalGridContextRenderArgs) => ReactNode
  renderFieldCell?: (args: HorizontalGridFieldRenderArgs) => ReactNode
  renderCellActions?: (args: HorizontalGridFieldRenderArgs) => ReactNode
  renderRowActions?: (row: HorizontalGridRow) => ReactNode
  selectedCandidateId?: string | null
}

function DefaultContextCell({ cell }: HorizontalGridContextRenderArgs) {
  return (
    <Stack spacing={0.25} minWidth={0}>
      <Typography fontWeight={750} noWrap variant="body2">
        {cell.value.identityLabel}
      </Typography>
      {cell.value.secondaryLabel ? (
        <Typography color="text.secondary" noWrap variant="caption">
          {cell.value.secondaryLabel}
        </Typography>
      ) : null}
    </Stack>
  )
}

function DefaultFieldCell({ cell }: HorizontalGridFieldRenderArgs) {
  if (!cell.hasField) {
    return (
      <Typography color="text.disabled" fontStyle="italic" variant="body2">
        Not available
      </Typography>
    )
  }

  const value = formatHorizontalGridValue(cell.value)
  return (
    <Typography
      aria-label={value === null ? 'Empty value' : undefined}
      sx={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}
      variant="body2"
    >
      {value ?? '—'}
    </Typography>
  )
}

function orderedFieldColumns(
  columns: readonly HorizontalGridColumn[],
  pinnedColumnKeys: readonly string[],
): HorizontalGridColumn[] {
  const fieldColumns = columns.filter((column) => column.kind === 'field')
  const columnsByKey = new Map(fieldColumns.map((column) => [column.key, column]))
  const pinnedColumns = pinnedColumnKeys.flatMap((key) => {
    const column = columnsByKey.get(key)
    return column ? [column] : []
  })
  const pinnedKeys = new Set(pinnedColumnKeys)

  return [...pinnedColumns, ...fieldColumns.filter((column) => !pinnedKeys.has(column.key))]
}

export default function HorizontalCurationGrid({
  model,
  caption = 'Curation records arranged by field',
  accessibleLabel = 'Horizontally scrollable curation grid',
  rowActionsLabel = 'Row actions',
  renderContextCell = (args) => <DefaultContextCell {...args} />,
  renderFieldCell = (args) => <DefaultFieldCell {...args} />,
  renderCellActions,
  renderRowActions,
  selectedCandidateId = null,
}: HorizontalCurationGridProps) {
  const theme = useTheme()
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const descriptionId = useId()
  const scrollRegionRef = useRef<HTMLDivElement>(null)
  const [density, setDensity] = useState<HorizontalGridDensity>('compact')
  const [pinnedColumnKeys, setPinnedColumnKeys] = useState<string[]>([])
  const [announcement, setAnnouncement] = useState('')
  const contextColumn = model.columns.find(
    (column) => column.key === HORIZONTAL_GRID_CONTEXT_COLUMN_KEY && column.kind === 'context',
  )
  const activePinnedColumnKeys = useMemo(
    () => {
      const fieldColumnKeys = new Set(
        model.columns.filter((column) => column.kind === 'field').map((column) => column.key),
      )
      return pinnedColumnKeys.filter((key) => fieldColumnKeys.has(key))
    },
    [model.columns, pinnedColumnKeys],
  )
  const displayColumns = useMemo(
    () => orderedFieldColumns(model.columns, activePinnedColumnKeys),
    [activePinnedColumnKeys, model.columns],
  )

  useEffect(() => {
    const scrollRegion = scrollRegionRef.current
    if (!scrollRegion) {
      return
    }

    const handleWheel = (event: WheelEvent) => {
      if (!event.shiftKey || Math.abs(event.deltaY) < Math.abs(event.deltaX)) {
        return
      }

      event.preventDefault()
      scrollRegion.scrollLeft += event.deltaY
    }

    scrollRegion.addEventListener('wheel', handleWheel, { passive: false })
    return () => scrollRegion.removeEventListener('wheel', handleWheel)
  }, [])

  if (!contextColumn) {
    throw new Error('Horizontal grid model requires its canonical context column')
  }

  const tableMinWidth =
    CONTEXT_COLUMN_WIDTH + displayColumns.length * FIELD_COLUMN_WIDTH + ACTION_COLUMN_WIDTH
  const rowHeight = density === 'compact' ? 68 : 104
  const surfaceColor = theme.palette.background.paper
  // Sticky cells must be opaque or scrolled text remains visible beneath them.
  const headerColor = theme.palette.mode === 'dark'
    ? theme.palette.grey[900]
    : theme.palette.grey[50]
  const pinnedColor = theme.palette.mode === 'dark'
    ? theme.palette.grey[800]
    : theme.palette.grey[100]
  const selectedRow = model.rows.find((row) => row.candidateId === selectedCandidateId) ?? null

  const togglePin = (column: HorizontalGridColumn) => {
    const isPinned = pinnedColumnKeys.includes(column.key)
    setPinnedColumnKeys((current) =>
      isPinned ? current.filter((key) => key !== column.key) : [...current, column.key],
    )
    setAnnouncement(
      isPinned
        ? `${column.label} column unpinned`
        : `${column.label} column pinned beside ${contextColumn.label}`,
    )
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) {
      return
    }

    if (event.key === 'ArrowRight') {
      event.preventDefault()
      event.currentTarget.scrollLeft += FIELD_COLUMN_WIDTH
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      event.currentTarget.scrollLeft -= FIELD_COLUMN_WIDTH
    }
  }

  const stickyCellSx = (
    side: 'left' | 'right',
    offset: number,
    isHeader: boolean,
    isLastPinned = false,
  ) => ({
    [side]: offset,
    position: 'sticky' as const,
    zIndex: isHeader ? 6 : 4,
    backgroundColor: isHeader ? (side === 'left' ? pinnedColor : headerColor) : surfaceColor,
    boxShadow: isLastPinned
      ? `5px 0 10px ${alpha(theme.palette.common.black, 0.18)}`
      : side === 'right'
        ? `-5px 0 10px ${alpha(theme.palette.common.black, 0.18)}`
        : undefined,
  })

  return (
    <Box
      data-density={density}
      data-reduced-motion={reducedMotion ? 'true' : 'false'}
      data-theme-mode={theme.palette.mode}
      data-testid="horizontal-curation-grid"
      sx={{
        position: 'relative',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: 1,
        backgroundColor: surfaceColor,
        '@media (prefers-reduced-motion: reduce)': {
          '&, & *': {
            scrollBehavior: 'auto !important',
            transitionDuration: '0.01ms !important',
            animationDuration: '0.01ms !important',
          },
        },
      }}
    >
      <Stack
        alignItems="center"
        direction="row"
        flexWrap="wrap"
        gap={1}
        sx={{
          minHeight: 52,
          px: 1.25,
          py: 0.75,
          borderBottom: `1px solid ${theme.palette.divider}`,
          backgroundColor: headerColor,
        }}
      >
        <Button
          aria-label={
            activePinnedColumnKeys.length === 0
              ? 'No optional pinned columns to clear'
              : `Clear ${activePinnedColumnKeys.length} optional pinned ${
                  activePinnedColumnKeys.length === 1 ? 'column' : 'columns'
                }`
          }
          disabled={activePinnedColumnKeys.length === 0}
          onClick={() => {
            const clearedCount = activePinnedColumnKeys.length
            setPinnedColumnKeys([])
            setAnnouncement(
              `${clearedCount} optional pinned ${
                clearedCount === 1 ? 'column' : 'columns'
              } cleared; ${contextColumn.label} remains pinned`,
            )
          }}
          size="small"
          startIcon={<ClearAllRoundedIcon />}
          sx={{ textTransform: 'none' }}
          variant="outlined"
        >
          Clear pins
        </Button>

        <Stack
          alignItems="center"
          aria-label="Row density"
          direction="row"
          role="group"
          spacing={0.5}
        >
          <Typography color="text.secondary" fontWeight={700} variant="caption">
            Rows
          </Typography>
          {(['compact', 'comfortable'] as const).map((option) => (
            <Button
              aria-pressed={density === option}
              key={option}
              onClick={() => {
                setDensity(option)
                setAnnouncement(`${option === 'compact' ? 'Compact' : 'Comfortable'} row density enabled`)
              }}
              size="small"
              sx={{ minWidth: 0, textTransform: 'none' }}
              variant={density === option ? 'contained' : 'text'}
            >
              {option === 'compact' ? 'Compact' : 'Comfortable'}
            </Button>
          ))}
        </Stack>

        <Typography
          color="text.secondary"
          id={descriptionId}
          sx={{ ml: { sm: 'auto' }, width: { xs: '100%', sm: 'auto' } }}
          variant="caption"
        >
          Use Left and Right arrows, or Shift + wheel, to move across fields.
        </Typography>
      </Stack>

      <TableContainer
        aria-describedby={descriptionId}
        aria-label={accessibleLabel}
        component={Box}
        data-testid="horizontal-grid-scroll-region"
        onKeyDown={handleKeyDown}
        ref={scrollRegionRef}
        role="region"
        tabIndex={0}
        sx={{
          minHeight: 0,
          flex: 1,
          overflow: 'auto',
          overscrollBehavior: 'contain',
          scrollBehavior: reducedMotion ? 'auto' : 'smooth',
          '&:focus-visible': {
            outline: `3px solid ${theme.palette.primary.main}`,
            outlineOffset: -3,
          },
        }}
      >
        <Table
          data-testid="horizontal-grid-table"
          size="small"
          sx={{
            width: tableMinWidth,
            minWidth: tableMinWidth,
            tableLayout: 'fixed',
            borderCollapse: 'separate',
            borderSpacing: 0,
            transition: reducedMotion
              ? 'none'
              : theme.transitions.create('background-color', {
                  duration: theme.transitions.duration.shortest,
                }),
          }}
        >
          <caption
            style={{
              position: 'absolute',
              width: 1,
              height: 1,
              padding: 0,
              margin: -1,
              overflow: 'hidden',
              clip: 'rect(0, 0, 0, 0)',
              whiteSpace: 'nowrap',
              border: 0,
            }}
          >
            {caption}
          </caption>
          <colgroup>
            <col style={{ width: CONTEXT_COLUMN_WIDTH }} />
            {displayColumns.map((column) => (
              <col key={column.key} style={{ width: FIELD_COLUMN_WIDTH }} />
            ))}
            <col style={{ width: ACTION_COLUMN_WIDTH }} />
          </colgroup>
          <TableHead>
            <TableRow>
              <TableCell
                data-column-key={contextColumn.key}
                data-sticky="left"
                scope="col"
                sx={{
                  ...stickyCellSx('left', 0, true, activePinnedColumnKeys.length === 0),
                  top: 0,
                  minWidth: CONTEXT_COLUMN_WIDTH,
                  borderRight: `1px solid ${theme.palette.divider}`,
                  borderBottom: `1px solid ${theme.palette.divider}`,
                }}
              >
                <Stack alignItems="center" direction="row" justifyContent="space-between" spacing={1}>
                  <Box minWidth={0}>
                    <Typography fontWeight={800} noWrap variant="caption">
                      {contextColumn.label}
                    </Typography>
                    <Typography color="text.secondary" display="block" variant="caption">
                      Identity / context
                    </Typography>
                  </Box>
                  <PushPinRoundedIcon
                    aria-label={`${contextColumn.label} is always pinned`}
                    color="primary"
                    fontSize="small"
                  />
                </Stack>
              </TableCell>

              {displayColumns.map((column) => {
                const pinnedIndex = activePinnedColumnKeys.indexOf(column.key)
                const isPinned = pinnedIndex >= 0
                const isLastPinned = isPinned && pinnedIndex === activePinnedColumnKeys.length - 1

                return (
                  <TableCell
                    data-column-key={column.key}
                    data-pinned={isPinned ? 'true' : 'false'}
                    key={column.key}
                    scope="col"
                    sx={{
                      ...(isPinned
                        ? stickyCellSx(
                            'left',
                            CONTEXT_COLUMN_WIDTH + pinnedIndex * FIELD_COLUMN_WIDTH,
                            true,
                            isLastPinned,
                          )
                        : {
                            position: 'sticky' as const,
                            backgroundColor: headerColor,
                            zIndex: 3,
                          }),
                      top: 0,
                      minWidth: FIELD_COLUMN_WIDTH,
                      borderRight: `1px solid ${theme.palette.divider}`,
                      borderBottom: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    <Stack alignItems="center" direction="row" justifyContent="space-between" spacing={0.5}>
                      <Box minWidth={0}>
                        <Typography fontWeight={800} noWrap variant="caption">
                          {column.label}
                        </Typography>
                        {column.groupLabel ? (
                          <Typography color="text.secondary" display="block" noWrap variant="caption">
                            {column.groupLabel}
                          </Typography>
                        ) : null}
                      </Box>
                      <IconButton
                        aria-label={`${isPinned ? 'Unpin' : 'Pin'} ${column.label} column`}
                        aria-pressed={isPinned}
                        color={isPinned ? 'primary' : 'default'}
                        onClick={() => togglePin(column)}
                        size="small"
                        sx={{
                          flexShrink: 0,
                          '&:focus-visible': {
                            outline: `2px solid ${theme.palette.primary.main}`,
                            outlineOffset: 1,
                          },
                        }}
                      >
                        {isPinned ? (
                          <PushPinRoundedIcon fontSize="small" />
                        ) : (
                          <PushPinOutlinedIcon fontSize="small" />
                        )}
                      </IconButton>
                    </Stack>
                  </TableCell>
                )
              })}

              <TableCell
                align="center"
                data-sticky="right"
                scope="col"
                sx={{
                  ...stickyCellSx('right', 0, true),
                  top: 0,
                  minWidth: ACTION_COLUMN_WIDTH,
                  borderBottom: `1px solid ${theme.palette.divider}`,
                }}
              >
                <Typography fontWeight={800} variant="caption">
                  {rowActionsLabel}
                </Typography>
              </TableCell>
            </TableRow>
          </TableHead>

          <TableBody>
            {model.rows.length === 0 ? (
              <TableRow>
                <TableCell
                  align="center"
                  colSpan={displayColumns.length + 2}
                  sx={{ height: rowHeight, color: 'text.secondary' }}
                >
                  No curation rows
                </TableCell>
              </TableRow>
            ) : (
              model.rows.map((row) => {
                const cellsByColumnKey = new Map(row.cells.map((cell) => [cell.columnKey, cell]))

                return (
                    <TableRow
                      data-candidate-id={row.candidateId}
                      data-selected={row.candidateId === selectedCandidateId ? 'true' : 'false'}
                      key={row.candidateId}
                      sx={{
                        height: rowHeight,
                        '& > *': row.candidateId === selectedCandidateId
                          ? { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: -2 }
                          : undefined,
                      }}
                    >
                    <TableCell
                      component="th"
                      data-column-key={contextColumn.key}
                      data-sticky="left"
                      scope="row"
                      sx={{
                        ...stickyCellSx('left', 0, false, activePinnedColumnKeys.length === 0),
                        minWidth: CONTEXT_COLUMN_WIDTH,
                        px: 1.25,
                        py: density === 'compact' ? 1 : 2,
                        borderRight: `1px solid ${theme.palette.divider}`,
                        borderBottom: `1px solid ${theme.palette.divider}`,
                        verticalAlign: 'top',
                      }}
                    >
                      {renderContextCell({ cell: row.contextCell, row })}
                    </TableCell>

                    {displayColumns.map((column) => {
                      const cell = cellsByColumnKey.get(column.key)
                      if (!cell) {
                        throw new Error(`Horizontal grid row is missing cell for column ${column.key}`)
                      }

                      const pinnedIndex = activePinnedColumnKeys.indexOf(column.key)
                      const isPinned = pinnedIndex >= 0
                      const isLastPinned =
                        isPinned && pinnedIndex === activePinnedColumnKeys.length - 1
                      const renderArgs = { cell, column, row }

                      return (
                        <TableCell
                          data-column-key={column.key}
                          data-has-field={cell.hasField ? 'true' : 'false'}
                          data-pinned={isPinned ? 'true' : 'false'}
                          key={column.key}
                          sx={{
                            ...(isPinned
                              ? stickyCellSx(
                                  'left',
                                  CONTEXT_COLUMN_WIDTH + pinnedIndex * FIELD_COLUMN_WIDTH,
                                  false,
                                  isLastPinned,
                                )
                              : { backgroundColor: surfaceColor }),
                            minWidth: FIELD_COLUMN_WIDTH,
                            px: 1.25,
                            py: density === 'compact' ? 1 : 2,
                            borderRight: `1px solid ${theme.palette.divider}`,
                            borderBottom: `1px solid ${theme.palette.divider}`,
                            verticalAlign: 'top',
                            transition: reducedMotion
                              ? 'none'
                              : theme.transitions.create('background-color', {
                                  duration: theme.transitions.duration.shortest,
                                }),
                            '&:focus-within': {
                              outline: `2px solid ${theme.palette.primary.main}`,
                              outlineOffset: -2,
                            },
                          }}
                        >
                          <Stack height="100%" justifyContent="space-between" spacing={1}>
                            {renderFieldCell(renderArgs)}
                            {renderCellActions ? (
                              <Box data-slot="cell-actions">{renderCellActions(renderArgs)}</Box>
                            ) : null}
                          </Stack>
                        </TableCell>
                      )
                    })}

                    <TableCell
                      align="center"
                      data-slot="row-actions"
                      data-sticky="right"
                      sx={{
                        ...stickyCellSx('right', 0, false),
                        minWidth: ACTION_COLUMN_WIDTH,
                        px: 0.75,
                        py: density === 'compact' ? 1 : 2,
                        borderBottom: `1px solid ${theme.palette.divider}`,
                        verticalAlign: 'middle',
                      }}
                    >
                      {renderRowActions ? renderRowActions(row) : null}
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Stack
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        spacing={0.75}
        sx={{
          borderTop: `1px solid ${theme.palette.divider}`,
          backgroundColor: headerColor,
          minHeight: 38,
          px: 1.25,
          py: 0.65,
        }}
      >
        <Typography color="text.secondary" data-testid="horizontal-grid-selected-record" variant="caption">
          {selectedRow ? (
            <><strong>{selectedRow.contextCell.value.identityLabel}</strong> selected</>
          ) : 'Select a record to inspect its evidence'}
        </Typography>
        <Stack alignItems="center" direction="row" flexWrap="wrap" spacing={1.25} useFlexGap>
          <Stack alignItems="center" direction="row" spacing={0.4}>
            <PushPinOutlinedIcon color="action" sx={{ fontSize: 15 }} />
            <Typography color="text.secondary" variant="caption">Pin headers</Typography>
          </Stack>
          <Stack alignItems="center" direction="row" spacing={0.4}>
            <FindInPageOutlinedIcon color="action" sx={{ fontSize: 15 }} />
            <Typography color="text.secondary" variant="caption">Evidence</Typography>
          </Stack>
          <Stack alignItems="center" direction="row" spacing={0.4}>
            <EditOutlinedIcon color="action" sx={{ fontSize: 15 }} />
            <Typography color="text.secondary" variant="caption">Edit</Typography>
          </Stack>
          <Typography color="text.secondary" variant="caption">
            <Box component="kbd" sx={{ border: `1px solid ${theme.palette.divider}`, borderRadius: 0.5, px: 0.5, py: 0.15 }}>Shift</Box>
            {' + scroll across fields'}
          </Typography>
        </Stack>
      </Stack>

      <Box
        aria-atomic="true"
        aria-live="polite"
        role="status"
        sx={{
          position: 'absolute',
          width: 1,
          height: 1,
          p: 0,
          m: -1,
          overflow: 'hidden',
          clip: 'rect(0, 0, 0, 0)',
          whiteSpace: 'nowrap',
          border: 0,
        }}
      >
        {announcement}
      </Box>
    </Box>
  )
}
