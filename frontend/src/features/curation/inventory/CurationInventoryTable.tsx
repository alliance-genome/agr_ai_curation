import {
  Alert,
  Box,
  Button,
  Chip,
  FormControl,
  MenuItem,
  Pagination,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import { useState } from 'react'

import type {
  CurationPageInfo,
  CurationSessionFilters,
  CurationSessionSortField,
  CurationSessionSummary,
  CurationSortDirection,
} from '../types'
import BatchGroupRow from './BatchGroupRow'
import { useCurationFlowRunList } from './curationInventoryService'
import {
  formatLastWorkedAt,
  formatSessionDate,
  getAdapterChipColor,
  getAdapterLabel,
  getEvidenceLabel,
  getEvidenceTone,
  getStatusChipColor,
  getStatusLabel,
  getValidationLabel,
  getValidationSegmentStyles,
} from './inventoryPresentation'

const COLUMN_COUNT = 9

type InventoryViewMode = 'sessions' | 'flow_runs'

const COLUMN_HEADERS: Array<{
  field: CurationSessionSortField
  label: string
}> = [
  { field: 'status', label: 'Status' },
  { field: 'document_title', label: 'Paper' },
  { field: 'adapter', label: 'Adapter / Profile' },
  { field: 'candidate_count', label: 'Candidates' },
  { field: 'validation', label: 'Validation' },
  { field: 'evidence', label: 'Evidence' },
  { field: 'prepared_at', label: 'Prepared' },
  { field: 'last_worked_at', label: 'Last Worked' },
  { field: 'curator', label: 'Curator' },
]

interface CurationInventoryTableProps {
  filters: CurationSessionFilters
  sessions: CurationSessionSummary[]
  pageInfo?: CurationPageInfo
  sortBy: CurationSessionSortField
  sortDirection: CurationSortDirection
  hasActiveFilters: boolean
  isLoading: boolean
  isRefreshing: boolean
  errorMessage?: string
  onSortChange: (field: CurationSessionSortField) => void
  onRowClick: (sessionId: string) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  onRetry: () => void
  onClearFilters: () => void
}

interface SortableHeaderProps {
  active: boolean
  direction: CurationSortDirection
  field: CurationSessionSortField
  label: string
  onSortChange: (field: CurationSessionSortField) => void
}

interface SessionTableRowProps {
  nested?: boolean
  onRowClick: (sessionId: string) => void
  session: CurationSessionSummary
}

function SortableHeader({
  active,
  direction,
  field,
  label,
  onSortChange,
}: SortableHeaderProps) {
  return (
    <TableCell sortDirection={active ? direction : false}>
      <TableSortLabel
        active={active}
        direction={active ? direction : 'asc'}
        onClick={() => onSortChange(field)}
      >
        {label}
      </TableSortLabel>
    </TableCell>
  )
}

function renderCuratorName(session: CurationSessionSummary): string {
  return (
    session.assigned_curator?.display_name ||
    session.assigned_curator?.email ||
    session.assigned_curator?.actor_id ||
    'Unassigned'
  )
}

function renderCandidateSummary(session: CurationSessionSummary): string {
  return `${session.progress.reviewed_candidates} reviewed`
}

function renderRangeLabel(pageInfo?: CurationPageInfo): string {
  if (!pageInfo || pageInfo.total_items === 0) {
    return 'Showing 0 sessions'
  }

  const start = (pageInfo.page - 1) * pageInfo.page_size + 1
  const end = Math.min(pageInfo.page * pageInfo.page_size, pageInfo.total_items)

  return `Showing ${start}-${end} of ${pageInfo.total_items} sessions`
}

function renderFlowRunRangeLabel(flowRunCount: number): string {
  return `Showing ${flowRunCount} flow runs`
}

function SessionTableRow({ nested = false, onRowClick, session }: SessionTableRowProps) {
  const theme = useTheme()
  const validationSegments = getValidationSegmentStyles(theme, session.validation)

  return (
    <TableRow
      hover
      key={session.session_id}
      onClick={() => onRowClick(session.session_id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onRowClick(session.session_id)
        }
      }}
      role="button"
      sx={{
        backgroundColor: nested ? alpha(theme.palette.info.main, 0.04) : undefined,
        cursor: 'pointer',
        '& td': {
          borderBottomColor: 'divider',
        },
      }}
      tabIndex={0}
    >
      <TableCell>
        <Chip
          color={getStatusChipColor(session.status)}
          label={getStatusLabel(session.status)}
          size="small"
          variant={session.status === 'paused' ? 'outlined' : 'filled'}
        />
      </TableCell>
      <TableCell sx={{ maxWidth: 340, pl: nested ? 4 : 2 }}>
        <Stack spacing={0.5}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {session.document.title}
          </Typography>
          <Typography color="text.secondary" variant="caption">
            {nested && session.flow_run_id ? `Flow run ${session.flow_run_id} • ` : ''}
            {session.document.pmid
              ? `PMID ${session.document.pmid}`
              : session.document.doi
                ? `DOI ${session.document.doi}`
                : session.document.document_id}
          </Typography>
        </Stack>
      </TableCell>
      <TableCell>
        <Chip
          color={getAdapterChipColor(session.adapter)}
          label={getAdapterLabel(session.adapter)}
          size="small"
          sx={{
            backgroundColor: alpha(theme.palette[getAdapterChipColor(session.adapter)].main, 0.14),
            borderColor: alpha(theme.palette[getAdapterChipColor(session.adapter)].main, 0.28),
            color: theme.palette[getAdapterChipColor(session.adapter)].light,
          }}
          variant="outlined"
        />
      </TableCell>
      <TableCell>
        <Stack spacing={0.35}>
          <Typography variant="body2">{session.progress.total_candidates} total</Typography>
          <Typography color="text.secondary" variant="caption">
            {renderCandidateSummary(session)}
          </Typography>
        </Stack>
      </TableCell>
      <TableCell>
        <Stack spacing={0.75}>
          <Box sx={{ display: 'flex', gap: 0.5, width: 108 }}>
            {validationSegments.map((segment, index) => (
              <Box
                key={`${session.session_id}-validation-${index}`}
                sx={{
                  backgroundColor: segment.color,
                  borderRadius: 999,
                  flex: segment.flex,
                  height: 8,
                }}
              />
            ))}
          </Box>
          <Typography color="text.secondary" variant="caption">
            {getValidationLabel(session.validation)}
          </Typography>
        </Stack>
      </TableCell>
      <TableCell>
        <Typography
          sx={{
            color: getEvidenceTone(theme, session.evidence),
            fontWeight: 500,
          }}
          variant="body2"
        >
          {getEvidenceLabel(session.evidence)}
        </Typography>
      </TableCell>
      <TableCell>
        <Typography variant="body2">{formatSessionDate(session.prepared_at)}</Typography>
      </TableCell>
      <TableCell>
        <Stack spacing={0.35}>
          <Typography variant="body2">{formatLastWorkedAt(session.last_worked_at)}</Typography>
          {session.last_worked_at && (
            <Typography color="text.secondary" variant="caption">
              {formatSessionDate(session.last_worked_at)}
            </Typography>
          )}
        </Stack>
      </TableCell>
      <TableCell>
        <Typography variant="body2">{renderCuratorName(session)}</Typography>
      </TableCell>
    </TableRow>
  )
}

export default function CurationInventoryTable({
  filters,
  sessions,
  pageInfo,
  sortBy,
  sortDirection,
  hasActiveFilters,
  isLoading,
  isRefreshing,
  errorMessage,
  onSortChange,
  onRowClick,
  onPageChange,
  onPageSizeChange,
  onRetry,
  onClearFilters,
}: CurationInventoryTableProps) {
  const [viewMode, setViewMode] = useState<InventoryViewMode>('sessions')
  const flowRunListQuery = useCurationFlowRunList(
    {
      filters,
    },
    {
      enabled: viewMode === 'flow_runs',
    }
  )

  const flowRunErrorMessage = flowRunListQuery.error instanceof Error
    ? flowRunListQuery.error.message
    : undefined
  const flowRuns = flowRunListQuery.data?.flow_runs ?? []
  const tableErrorMessage = viewMode === 'flow_runs' ? flowRunErrorMessage : errorMessage
  const tableIsLoading = viewMode === 'flow_runs' ? flowRunListQuery.isLoading : isLoading
  const tableIsRefreshing = viewMode === 'flow_runs' ? flowRunListQuery.isFetching : isRefreshing

  const renderSessionRow = (
    session: CurationSessionSummary,
    options?: { nested?: boolean }
  ) => (
    <SessionTableRow
      key={`${options?.nested ? `nested-${session.session_id}` : session.session_id}`}
      nested={options?.nested}
      onRowClick={onRowClick}
      session={session}
    />
  )

  return (
    <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        spacing={1.5}
        alignItems={{ xs: 'stretch', md: 'center' }}
        justifyContent="space-between"
        sx={{
          borderBottom: '1px solid',
          borderBottomColor: 'divider',
          px: 2,
          py: 1.5,
        }}
      >
        <Stack spacing={0.25}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            Inventory view
          </Typography>
          <Typography color="text.secondary" variant="caption">
            Keep the flat list as the default, or browse sessions by shared flow run.
          </Typography>
        </Stack>
        <ToggleButtonGroup
          exclusive
          onChange={(_event, nextMode: InventoryViewMode | null) => {
            if (nextMode) {
              setViewMode(nextMode)
            }
          }}
          size="small"
          value={viewMode}
        >
          <ToggleButton value="sessions">Sessions</ToggleButton>
          <ToggleButton value="flow_runs">By flow run</ToggleButton>
        </ToggleButtonGroup>
      </Stack>

      <TableContainer sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 1220 }}>
          <TableHead>
            <TableRow>
              {viewMode === 'sessions'
                ? COLUMN_HEADERS.map((column) => (
                    <SortableHeader
                      active={sortBy === column.field}
                      direction={sortDirection}
                      field={column.field}
                      key={column.field}
                      label={column.label}
                      onSortChange={onSortChange}
                    />
                  ))
                : COLUMN_HEADERS.map((column) => (
                    <TableCell key={column.field}>{column.label}</TableCell>
                  ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {tableErrorMessage ? (
              <TableRow>
                <TableCell colSpan={COLUMN_COUNT} sx={{ py: 4 }}>
                  <Alert
                    action={
                      <Button
                        color="inherit"
                        onClick={() => {
                          if (viewMode === 'flow_runs') {
                            void flowRunListQuery.refetch()
                            return
                          }
                          onRetry()
                        }}
                        size="small"
                      >
                        Retry
                      </Button>
                    }
                    severity="error"
                  >
                    {tableErrorMessage}
                  </Alert>
                </TableCell>
              </TableRow>
            ) : viewMode === 'flow_runs' ? (
              flowRuns.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={COLUMN_COUNT} sx={{ py: 6 }}>
                    <Stack spacing={1.5} alignItems="center">
                      <Typography variant="h6">
                        {tableIsLoading
                          ? 'Loading flow runs...'
                          : 'No batch flow runs match these filters.'}
                      </Typography>
                      <Typography color="text.secondary" variant="body2">
                        {hasActiveFilters
                          ? 'Try clearing one or more filters to broaden the queue.'
                          : 'Grouped flow runs will appear here when multiple sessions share a batch identifier.'}
                      </Typography>
                      {hasActiveFilters && !tableIsLoading && (
                        <Button onClick={onClearFilters} variant="outlined">
                          Clear filters
                        </Button>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ) : (
                flowRuns.map((flowRun) => (
                  <BatchGroupRow
                    colSpan={COLUMN_COUNT}
                    filters={filters}
                    flowRun={flowRun}
                    key={flowRun.flow_run_id}
                    pageSize={pageInfo?.page_size ?? 25}
                    renderSessionRow={renderSessionRow}
                  />
                ))
              )
            ) : sessions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={COLUMN_COUNT} sx={{ py: 6 }}>
                  <Stack spacing={1.5} alignItems="center">
                    <Typography variant="h6">
                      {tableIsLoading ? 'Loading inventory...' : 'No curation sessions match these filters.'}
                    </Typography>
                    <Typography color="text.secondary" variant="body2">
                      {hasActiveFilters
                        ? 'Try clearing one or more filters to broaden the queue.'
                        : 'Prepared sessions will appear here once they are ready for review.'}
                    </Typography>
                    {hasActiveFilters && !tableIsLoading && (
                      <Button onClick={onClearFilters} variant="outlined">
                        Clear filters
                      </Button>
                    )}
                  </Stack>
                </TableCell>
              </TableRow>
            ) : (
              sessions.map((session) => renderSessionRow(session))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Stack
        direction={{ xs: 'column', md: 'row' }}
        spacing={2}
        alignItems={{ xs: 'stretch', md: 'center' }}
        justifyContent="space-between"
        sx={{
          borderTop: '1px solid',
          borderTopColor: 'divider',
          px: 2,
          py: 1.5,
        }}
      >
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
          <Typography color="text.secondary" variant="body2">
            {viewMode === 'flow_runs'
              ? renderFlowRunRangeLabel(flowRuns.length)
              : renderRangeLabel(pageInfo)}
          </Typography>
          {tableIsRefreshing && !tableIsLoading && (
            <Typography color="text.secondary" variant="caption">
              Updating...
            </Typography>
          )}
        </Stack>

        {viewMode === 'sessions' ? (
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1.5}
            alignItems={{ xs: 'stretch', sm: 'center' }}
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography color="text.secondary" variant="caption">
                Rows
              </Typography>
              <FormControl size="small">
                <Select
                  value={String(pageInfo?.page_size ?? 25)}
                  onChange={(event) => onPageSizeChange(Number(event.target.value))}
                >
                  {[10, 25, 50, 100].map((pageSizeOption) => (
                    <MenuItem key={pageSizeOption} value={String(pageSizeOption)}>
                      {pageSizeOption}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Stack>
            <Pagination
              color="primary"
              count={pageInfo?.total_pages || 1}
              onChange={(_event, nextPage) => onPageChange(nextPage)}
              page={pageInfo?.page || 1}
              siblingCount={1}
              size="small"
            />
          </Stack>
        ) : (
          <Typography color="text.secondary" variant="caption">
            Flow runs are ordered by latest activity. Expand a flow run to load its sessions.
          </Typography>
        )}
      </Stack>
    </Paper>
  )
}
