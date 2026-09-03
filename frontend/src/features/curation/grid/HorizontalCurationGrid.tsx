import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import ClearAllRoundedIcon from '@mui/icons-material/ClearAllRounded'
import CheckOutlinedIcon from '@mui/icons-material/CheckOutlined'
import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import FindInPageOutlinedIcon from '@mui/icons-material/FindInPageOutlined'
import PushPinOutlinedIcon from '@mui/icons-material/PushPinOutlined'
import PushPinRoundedIcon from '@mui/icons-material/PushPinRounded'
import {
  Box,
  Button,
  IconButton,
  Portal,
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
import { lighten } from '@mui/material/styles'

import {
  HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
  type HorizontalGridColumn,
  type HorizontalGridContextCell,
  type HorizontalGridFieldCell,
  type HorizontalGridModel,
  type HorizontalGridRow,
} from './horizontalGridModel'
import { formatHorizontalGridValue } from './horizontalGridFormatting'
import { horizontalGridValidationPreviewCounts } from './horizontalGridValidationPreview'

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
  validationPreviewNotice?: string
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
  validationPreviewNotice = '',
}: HorizontalCurationGridProps) {
  const theme = useTheme()
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const descriptionId = useId()
  const scrollRegionRef = useRef<HTMLDivElement>(null)
  const [density, setDensity] = useState<HorizontalGridDensity>('compact')
  const [pinnedColumnKeys, setPinnedColumnKeys] = useState<string[]>([])
  const [announcement, setAnnouncement] = useState('')
  const [validationSummaryOpen, setValidationSummaryOpen] = useState(false)
  const validationSummaryTriggerRef = useRef<HTMLButtonElement>(null)
  const validationSummaryCloseRef = useRef<HTMLButtonElement>(null)
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
  const validationPreviewCounts = useMemo(
    () => horizontalGridValidationPreviewCounts(model),
    [model],
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

  useEffect(() => {
    if (!validationSummaryOpen) {
      return
    }

    validationSummaryCloseRef.current?.focus()
    const handleEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      event.preventDefault()
      setValidationSummaryOpen(false)
      validationSummaryTriggerRef.current?.focus()
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [validationSummaryOpen])

  useEffect(() => {
    if (validationPreviewNotice) {
      setAnnouncement(validationPreviewNotice)
    }
  }, [validationPreviewNotice])

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
    : '#fafaf8'
  const pinnedColor = theme.palette.mode === 'dark'
    ? lighten(surfaceColor, 0.06)
    : '#f3f8f6'
  const alternateSurfaceColor = theme.palette.mode === 'dark'
    ? lighten(surfaceColor, 0.025)
    : '#fdfdfc'
  const hoverSurfaceColor = theme.palette.mode === 'dark'
    ? lighten(surfaceColor, 0.08)
    : '#eff8f6'
  const rightSurfaceColor = theme.palette.mode === 'dark'
    ? lighten(surfaceColor, 0.045)
    : '#f7f9f8'
  const fieldSurfaceColors = {
    resolved: {
      base: 'transparent',
      hover: hoverSurfaceColor,
    },
    'needs-review': {
      base: theme.palette.mode === 'dark' ? alpha(theme.palette.warning.main, 0.13) : '#fffaf0',
      hover: theme.palette.mode === 'dark' ? alpha(theme.palette.warning.main, 0.2) : '#fff1d6',
    },
    'ai-unconfirmed': {
      base: theme.palette.mode === 'dark' ? alpha(theme.palette.error.main, 0.14) : '#fbe5e0',
      hover: theme.palette.mode === 'dark' ? alpha(theme.palette.error.main, 0.22) : '#f8d9d2',
    },
  } as const
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
    bodyColor = surfaceColor,
  ) => ({
    [side]: offset,
    position: 'sticky' as const,
    zIndex: isHeader ? 6 : 4,
    backgroundColor: isHeader
      ? (side === 'left' ? pinnedColor : headerColor)
      : side === 'right'
        ? rightSurfaceColor
        : bodyColor,
    boxShadow: isLastPinned
      ? `5px 0 9px ${alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.28 : 0.1)}`
      : side === 'right'
        ? `-5px 0 10px ${alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.28 : 0.09)}`
        : undefined,
  })

  return (
    <Box
      data-density={density}
      data-reduced-motion={reducedMotion ? 'true' : 'false'}
      data-theme-mode={theme.palette.mode}
      data-testid="horizontal-curation-grid"
      sx={{
        flex: 1,
        position: 'relative',
        minHeight: 0,
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: 1,
        backgroundColor: surfaceColor,
        '& [data-slot="field-state-marker"]': {
          position: 'absolute',
          right: 5,
          top: 5,
          alignItems: 'center',
          backgroundColor: theme.palette.mode === 'dark' ? theme.palette.error.dark : '#d25b48',
          borderRadius: '50%',
          color: theme.palette.common.white,
          display: 'inline-flex',
          fontSize: 10,
          fontWeight: 850,
          height: 16,
          justifyContent: 'center',
          lineHeight: 1,
          width: 16,
        },
        '& [data-slot="field-state-marker"][data-severity="warning"]': {
          backgroundColor: theme.palette.mode === 'dark' ? theme.palette.warning.dark : '#c8872d',
        },
        '& [data-slot="field-state-marker-text"]': { display: 'none' },
        '&[data-density="comfortable"] [data-slot="field-state-marker"]': {
          position: 'static',
          width: 'auto',
          height: 'auto',
          borderRadius: 0,
          backgroundColor: 'transparent',
          color: theme.palette.mode === 'dark' ? theme.palette.error.light : '#ad3f31',
          display: 'block',
          fontSize: 9,
          fontWeight: 720,
          lineHeight: 1.2,
        },
        '&[data-density="comfortable"] [data-slot="field-state-marker"][data-severity="warning"]': {
          color: theme.palette.mode === 'dark' ? theme.palette.warning.light : '#8a5b0d',
        },
        '&[data-density="comfortable"] [data-slot="field-state-marker-icon"]': { display: 'none' },
        '&[data-density="comfortable"] [data-slot="field-state-marker-text"]': { display: 'block' },
        '&[data-density="comfortable"] [data-slot="field-value"]': { WebkitLineClamp: 2 },
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
        gap={2}
        justifyContent="space-between"
        sx={{
          minHeight: 66,
          px: '18px',
          py: '10px',
          borderBottom: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.mode === 'dark' ? headerColor : '#fcfcfb',
        }}
      >
        <Stack
          aria-label="Validation legend"
          direction="row"
          flexWrap="wrap"
          gap="18px"
          role="group"
          useFlexGap
        >
          {([
            ['✓', 'Curator validated', theme.palette.mode === 'dark' ? theme.palette.success.dark : '#0b7d72'],
            ['!', 'Needs review', theme.palette.mode === 'dark' ? theme.palette.warning.dark : '#c8882d'],
            ['×', 'Not validated', theme.palette.mode === 'dark' ? theme.palette.error.dark : '#d25b48'],
          ] as const).map(([symbol, label, color]) => (
            <Stack alignItems="center" direction="row" gap="6px" key={label}>
              <Box
                aria-hidden="true"
                component="span"
                sx={{
                  alignItems: 'center',
                  backgroundColor: color,
                  borderRadius: '50%',
                  color: theme.palette.common.white,
                  display: 'inline-flex',
                  fontSize: 10,
                  fontWeight: 900,
                  height: 17,
                  justifyContent: 'center',
                  width: 17,
                }}
              >
                {symbol}
              </Box>
              <Typography sx={{ fontSize: 11 }}>{label}</Typography>
            </Stack>
          ))}
        </Stack>

        <Stack alignItems="center" direction="row" flexWrap="wrap" gap="8px" useFlexGap>
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
            startIcon={<ClearAllRoundedIcon sx={{ fontSize: 14 }} />}
            sx={{ borderRadius: '5px', fontSize: 10, fontWeight: 700, height: 34, textTransform: 'none' }}
            variant="outlined"
          >
            Clear pins
          </Button>

        <Stack
          alignItems="center"
          aria-label="Row density"
          direction="row"
          role="group"
          spacing={0.25}
          sx={{ border: `1px solid ${theme.palette.divider}`, borderRadius: '5px', height: 34, p: '2px' }}
        >
          <Typography color="text.secondary" sx={{ fontSize: 9, fontWeight: 760, letterSpacing: '0.07em', px: '7px', textTransform: 'uppercase' }}>
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
              sx={{
                borderRadius: '3px',
                backgroundColor: density === option
                  ? (theme.palette.mode === 'dark' ? '#17486f' : '#0b2f55')
                  : 'transparent',
                color: density === option ? theme.palette.common.white : 'text.secondary',
                fontSize: 10,
                fontWeight: 700,
                height: 28,
                minWidth: 0,
                px: '8px',
                textTransform: 'none',
                '&:hover': {
                  backgroundColor: density === option
                    ? (theme.palette.mode === 'dark' ? '#17486f' : '#0b2f55')
                    : (theme.palette.mode === 'dark' ? theme.palette.action.hover : '#edf3f2'),
                  color: density === option ? theme.palette.common.white : 'text.primary',
                },
              }}
              variant="text"
            >
              {option === 'compact' ? 'Compact' : 'Comfortable'}
            </Button>
          ))}
        </Stack>

          <Button
            aria-controls="horizontal-grid-validation-summary"
            aria-expanded={validationSummaryOpen}
            onClick={() => setValidationSummaryOpen(true)}
            ref={validationSummaryTriggerRef}
            size="small"
            sx={{
              borderRadius: '5px',
              color: 'text.primary',
              fontSize: 11,
              fontWeight: 650,
              minWidth: 0,
              px: '9px',
              py: '8px',
              textTransform: 'none',
              '&:hover': {
                backgroundColor: theme.palette.mode === 'dark'
                  ? theme.palette.action.hover
                  : '#edf1f1',
              },
            }}
            variant="text"
          >
            Validation summary&nbsp;<span aria-hidden="true">⌄</span>
          </Button>

          <Typography
            color="text.secondary"
            id={descriptionId}
            sx={{
              position: 'absolute',
              width: 1,
              height: 1,
              p: 0,
              m: -1,
              overflow: 'hidden',
              clip: 'rect(0, 0, 0, 0)',
              whiteSpace: 'nowrap',
            }}
          >
            Use Left and Right arrows, or Shift + wheel, to move across fields.
          </Typography>
        </Stack>
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
                  height: 44,
                  minWidth: CONTEXT_COLUMN_WIDTH,
                  px: '9px',
                  py: '8px',
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
                  <IconButton
                    aria-label={`${contextColumn.label} is always pinned`}
                    aria-pressed="true"
                    onClick={() => setAnnouncement(
                      `${contextColumn.label} stays pinned so source context remains visible`,
                    )}
                    size="small"
                    title={`${contextColumn.label} stays pinned so source context remains visible`}
                    sx={{
                      border: `1px solid ${theme.palette.mode === 'dark' ? theme.palette.divider : '#b4d5d0'}`,
                      borderRadius: '4px',
                      backgroundColor: theme.palette.mode === 'dark'
                        ? alpha(theme.palette.success.main, 0.16)
                        : '#e3f2ef',
                      color: theme.palette.mode === 'dark' ? theme.palette.success.light : '#176c66',
                      cursor: 'help',
                      height: 20,
                      p: 0,
                      width: 20,
                      '& svg': { fontSize: 13 },
                    }}
                  >
                    <PushPinRoundedIcon />
                  </IconButton>
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
                      height: 44,
                      minWidth: FIELD_COLUMN_WIDTH,
                      px: '9px',
                      py: '8px',
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
                        onClick={() => togglePin(column)}
                        size="small"
                        sx={{
                          border: `1px solid ${isPinned && theme.palette.mode === 'light' ? '#b4d5d0' : 'transparent'}`,
                          borderRadius: '4px',
                          backgroundColor: isPinned
                            ? (theme.palette.mode === 'dark'
                                ? alpha(theme.palette.success.main, 0.16)
                                : '#e3f2ef')
                            : 'transparent',
                          color: isPinned
                            ? (theme.palette.mode === 'dark' ? theme.palette.success.light : '#176c66')
                            : (theme.palette.mode === 'dark' ? theme.palette.text.secondary : '#809094'),
                          flexShrink: 0,
                          height: 20,
                          p: 0,
                          width: 20,
                          '& svg': { fontSize: 13 },
                          '&:hover': {
                            borderColor: theme.palette.mode === 'dark' ? theme.palette.divider : '#bad1ce',
                            backgroundColor: theme.palette.mode === 'dark'
                              ? theme.palette.action.hover
                              : '#f1f7f5',
                            color: theme.palette.mode === 'dark' ? theme.palette.success.light : '#076b65',
                          },
                          '&:focus-visible': {
                            outline: `2px solid ${theme.palette.mode === 'dark' ? theme.palette.primary.light : '#17486f'}`,
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
                  height: 44,
                  minWidth: ACTION_COLUMN_WIDTH,
                  px: '9px',
                  py: '8px',
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
              model.rows.map((row, rowIndex) => {
                const cellsByColumnKey = new Map(row.cells.map((cell) => [cell.columnKey, cell]))
                const rowSurfaceColor = rowIndex % 2 === 0 ? surfaceColor : alternateSurfaceColor

                return (
                    <TableRow
                      aria-selected={row.candidateId === selectedCandidateId}
                      data-candidate-id={row.candidateId}
                      data-selected={row.candidateId === selectedCandidateId ? 'true' : 'false'}
                      key={row.candidateId}
                      sx={{ height: rowHeight }}
                    >
                    <TableCell
                      component="th"
                      data-column-key={contextColumn.key}
                      data-sticky="left"
                      scope="row"
                      sx={{
                        ...stickyCellSx(
                          'left',
                          0,
                          false,
                          activePinnedColumnKeys.length === 0,
                          rowSurfaceColor,
                        ),
                        minWidth: CONTEXT_COLUMN_WIDTH,
                        p: 0,
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
                      const fieldColors = cell.state
                        ? fieldSurfaceColors[cell.state]
                        : {
                            base: theme.palette.mode === 'dark'
                              ? lighten(surfaceColor, 0.035)
                              : '#f7f8f6',
                            hover: theme.palette.mode === 'dark'
                              ? lighten(surfaceColor, 0.075)
                              : '#eef2f0',
                          }

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
                                  rowSurfaceColor,
                                )
                              : { backgroundColor: rowSurfaceColor }),
                            minWidth: FIELD_COLUMN_WIDTH,
                            p: 0,
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
                          <Box
                            data-field-state={cell.state ?? 'neutral'}
                            sx={{
                              backgroundColor: fieldColors.base,
                              color: cell.state === null ? 'text.secondary' : 'text.primary',
                              height: '100%',
                              minHeight: rowHeight,
                              p: '6px 8px 30px 9px',
                              position: 'relative',
                              textAlign: 'left',
                              transition: reducedMotion
                                ? 'none'
                                : theme.transitions.create('background-color', {
                                    duration: theme.transitions.duration.shortest,
                                  }),
                              '&:hover': { backgroundColor: fieldColors.hover },
                            }}
                          >
                            {renderFieldCell(renderArgs)}
                            {renderCellActions ? (
                              <Box
                                data-slot="cell-actions"
                                sx={{ bottom: 4, left: 7, position: 'absolute', zIndex: 2 }}
                              >
                                {renderCellActions(renderArgs)}
                              </Box>
                            ) : null}
                          </Box>
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
                        px: '3px',
                        py: '2px',
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
            <CheckOutlinedIcon color="action" sx={{ fontSize: 15 }} />
            <Typography color="text.secondary" variant="caption">Validate</Typography>
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

      <Portal>
      <Box
        aria-hidden={!validationSummaryOpen}
        aria-labelledby="horizontal-grid-validation-summary-title"
        aria-modal="false"
        data-testid="horizontal-grid-validation-summary"
        id="horizontal-grid-validation-summary"
        role="dialog"
        sx={{
          position: 'fixed',
          zIndex: theme.zIndex.modal,
          top: '64px',
          right: 0,
          width: 'min(360px, 92vw)',
          height: 'calc(100dvh - 64px)',
          p: '22px',
          borderLeft: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.background.paper,
          boxShadow: theme.palette.mode === 'dark'
            ? `-14px 0 34px ${alpha(theme.palette.common.black, 0.42)}`
            : '-14px 0 34px rgba(22, 45, 54, 0.18)',
          pointerEvents: validationSummaryOpen ? 'auto' : 'none',
          transform: validationSummaryOpen ? 'translateX(0)' : 'translateX(105%)',
          transitionDuration: reducedMotion ? '0ms' : '220ms, 0ms',
          transitionDelay: validationSummaryOpen || reducedMotion ? '0ms' : '0ms, 220ms',
          transitionProperty: 'transform, visibility',
          transitionTimingFunction: 'ease, linear',
          visibility: validationSummaryOpen ? 'visible' : 'hidden',
        }}
      >
        <Box>
          <Typography
            color="text.secondary"
            sx={{
              display: 'block',
              mb: '3px',
              fontSize: 9,
              fontWeight: 770,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            Validation summary
          </Typography>
          <Typography
            component="h2"
            id="horizontal-grid-validation-summary-title"
            sx={{ fontSize: 15, fontWeight: 750, lineHeight: 1.25, m: 0, pr: 4 }}
          >
            {model.rows.length} {model.rows.length === 1 ? 'record' : 'records'} ·{' '}
            {validationPreviewCounts.total} curated fields
          </Typography>
        </Box>
        <IconButton
          aria-label="Close validation summary"
          onClick={() => {
            setValidationSummaryOpen(false)
            validationSummaryTriggerRef.current?.focus()
          }}
          ref={validationSummaryCloseRef}
          size="small"
          sx={{ position: 'absolute', right: 14, top: 16, height: 34, width: 34 }}
        >
          <CloseRoundedIcon fontSize="small" />
        </IconButton>
        <Box component="dl" sx={{ mt: '24px', mb: '24px', borderTop: `1px solid ${theme.palette.divider}` }}>
          {([
            ['✓', 'Curator validated', validationPreviewCounts.resolved, theme.palette.mode === 'dark' ? theme.palette.success.dark : '#0b7d72'],
            ['!', 'Needs review', validationPreviewCounts.needsReview, theme.palette.mode === 'dark' ? theme.palette.warning.dark : '#c8882d'],
            ['×', 'Not validated', validationPreviewCounts.notValidated, theme.palette.mode === 'dark' ? theme.palette.error.dark : '#d25b48'],
          ] as const).map(([symbol, label, count, color]) => (
            <Box
              component="div"
              key={label}
              sx={{
                alignItems: 'center',
                borderBottom: `1px solid ${theme.palette.divider}`,
                display: 'flex',
                justifyContent: 'space-between',
                py: '13px',
              }}
            >
              <Box component="dt" sx={{ alignItems: 'center', display: 'flex', gap: '8px', fontSize: 12 }}>
                <Box
                  aria-hidden="true"
                  component="span"
                  sx={{
                    alignItems: 'center',
                    backgroundColor: color,
                    borderRadius: '50%',
                    color: theme.palette.common.white,
                    display: 'inline-flex',
                    fontSize: 10,
                    fontWeight: 900,
                    height: 17,
                    justifyContent: 'center',
                    width: 17,
                  }}
                >
                  {symbol}
                </Box>
                {label}
              </Box>
              <Box component="dd" sx={{ fontSize: 17, fontWeight: 750, m: 0 }}>
                {count}
              </Box>
            </Box>
          ))}
        </Box>
        <Typography color="text.secondary" sx={{ fontSize: 12, lineHeight: 1.5 }}>
          Resolve blocking curated fields before final submission. Identity/context and unavailable
          fields do not participate in this summary.
        </Typography>
        <Typography color="text.secondary" sx={{ fontSize: 12, lineHeight: 1.5, mt: 1.5 }}>
          Preview only: checkmarks update this view but do not run or save validation.
        </Typography>
      </Box>
      </Portal>

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
