import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import {
  Alert,
  Button,
  Chip,
  CircularProgress,
  Pagination,
  Stack,
  TableCell,
  TableRow,
  Typography,
} from '@mui/material'
import { Fragment, type ReactNode, useState } from 'react'

import type {
  CurationFlowRunSummary,
  CurationPageInfo,
  CurationSessionFilters,
  CurationSessionSummary,
} from '../types'
import { formatSessionDate } from './inventoryPresentation'
import { useCurationFlowRunSessions } from './curationInventoryService'

interface BatchGroupRowProps {
  colSpan: number
  filters: CurationSessionFilters
  flowRun: CurationFlowRunSummary
  pageSize?: number
  renderSessionRow: (session: CurationSessionSummary, options?: { nested?: boolean }) => ReactNode
}

function renderRangeLabel(pageInfo?: CurationPageInfo): string {
  if (!pageInfo || pageInfo.total_items === 0) {
    return 'Showing 0 sessions'
  }

  const start = (pageInfo.page - 1) * pageInfo.page_size + 1
  const end = Math.min(pageInfo.page * pageInfo.page_size, pageInfo.total_items)

  return `Showing ${start}-${end} of ${pageInfo.total_items} sessions`
}

export default function BatchGroupRow({
  colSpan,
  filters,
  flowRun,
  pageSize = 25,
  renderSessionRow,
}: BatchGroupRowProps) {
  const [expanded, setExpanded] = useState(false)
  const [page, setPage] = useState(1)

  const sessionsQuery = useCurationFlowRunSessions(
    {
      flow_run_id: flowRun.flow_run_id,
      filters,
      page,
      page_size: pageSize,
    },
    {
      enabled: expanded,
    }
  )

  const errorMessage = sessionsQuery.error instanceof Error ? sessionsQuery.error.message : undefined
  const pageInfo = sessionsQuery.data?.page_info
  const sessions = sessionsQuery.data?.sessions ?? []

  const toggleExpanded = () => {
    setExpanded((currentValue) => !currentValue)
  }

  const flowRunLabel = flowRun.display_label || flowRun.flow_run_id

  return (
    <Fragment>
      <TableRow
        hover
        onClick={toggleExpanded}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            toggleExpanded()
          }
        }}
        role="button"
        sx={{
          backgroundColor: 'action.hover',
          cursor: 'pointer',
          '& td': {
            borderBottomColor: 'divider',
          },
        }}
        tabIndex={0}
      >
        <TableCell colSpan={colSpan}>
          <Stack
            direction={{ xs: 'column', md: 'row' }}
            spacing={1.5}
            alignItems={{ xs: 'flex-start', md: 'center' }}
            justifyContent="space-between"
          >
            <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
              {expanded ? (
                <KeyboardArrowDownIcon color="action" />
              ) : (
                <KeyboardArrowRightIcon color="action" />
              )}
              <Typography sx={{ fontWeight: 600 }} variant="body2">
                {`Flow run ${flowRunLabel}`}
              </Typography>
              <Chip label={`${flowRun.session_count} sessions`} size="small" variant="outlined" />
              <Chip label={`${flowRun.reviewed_count} reviewed`} size="small" variant="outlined" />
              <Chip label={`${flowRun.pending_count} pending`} size="small" variant="outlined" />
              <Chip label={`${flowRun.submitted_count} submitted`} size="small" variant="outlined" />
            </Stack>
            <Typography color="text.secondary" variant="caption">
              {flowRun.last_activity_at
                ? `Last activity ${formatSessionDate(flowRun.last_activity_at)}`
                : 'No activity yet'}
            </Typography>
          </Stack>
        </TableCell>
      </TableRow>

      {expanded && errorMessage && (
        <TableRow>
          <TableCell colSpan={colSpan} sx={{ py: 2 }}>
            <Alert
              action={
                <Button color="inherit" onClick={() => void sessionsQuery.refetch()} size="small">
                  Retry
                </Button>
              }
              severity="error"
            >
              {errorMessage}
            </Alert>
          </TableCell>
        </TableRow>
      )}

      {expanded && !errorMessage && sessionsQuery.isLoading && (
        <TableRow>
          <TableCell colSpan={colSpan} sx={{ py: 3 }}>
            <Stack direction="row" spacing={1.5} alignItems="center" justifyContent="center">
              <CircularProgress size={18} />
              <Typography color="text.secondary" variant="body2">
                Loading flow-run sessions...
              </Typography>
            </Stack>
          </TableCell>
        </TableRow>
      )}

      {expanded &&
        !errorMessage &&
        !sessionsQuery.isLoading &&
        sessions.map((session) => renderSessionRow(session, { nested: true }))}

      {expanded && !errorMessage && !sessionsQuery.isLoading && sessions.length === 0 && (
        <TableRow>
          <TableCell colSpan={colSpan} sx={{ py: 3 }}>
            <Typography color="text.secondary" variant="body2">
              No sessions matched this flow run.
            </Typography>
          </TableCell>
        </TableRow>
      )}

      {expanded && !errorMessage && pageInfo && pageInfo.total_pages > 1 && (
        <TableRow>
          <TableCell colSpan={colSpan} sx={{ py: 1.5 }}>
            <Stack
              direction={{ xs: 'column', md: 'row' }}
              spacing={1.5}
              alignItems={{ xs: 'stretch', md: 'center' }}
              justifyContent="space-between"
            >
              <Typography color="text.secondary" variant="caption">
                {renderRangeLabel(pageInfo)}
              </Typography>
              <Pagination
                color="primary"
                count={pageInfo.total_pages || 1}
                onChange={(_event, nextPage) => setPage(nextPage)}
                page={pageInfo.page || 1}
                size="small"
              />
            </Stack>
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  )
}
