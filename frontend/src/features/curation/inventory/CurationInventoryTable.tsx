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
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type {
  CurationPageInfo,
  CurationSessionSortField,
  CurationSessionSummary,
  CurationSortDirection,
} from '../types'
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

interface CurationInventoryTableProps {
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

export default function CurationInventoryTable({
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
  const theme = useTheme()

  return (
    <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
      <TableContainer sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 1220 }}>
          <TableHead>
            <TableRow>
              <SortableHeader
                active={sortBy === 'status'}
                direction={sortDirection}
                field="status"
                label="Status"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'document_title'}
                direction={sortDirection}
                field="document_title"
                label="Paper"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'adapter'}
                direction={sortDirection}
                field="adapter"
                label="Adapter / Profile"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'candidate_count'}
                direction={sortDirection}
                field="candidate_count"
                label="Candidates"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'validation'}
                direction={sortDirection}
                field="validation"
                label="Validation"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'evidence'}
                direction={sortDirection}
                field="evidence"
                label="Evidence"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'prepared_at'}
                direction={sortDirection}
                field="prepared_at"
                label="Prepared"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'last_worked_at'}
                direction={sortDirection}
                field="last_worked_at"
                label="Last Worked"
                onSortChange={onSortChange}
              />
              <SortableHeader
                active={sortBy === 'curator'}
                direction={sortDirection}
                field="curator"
                label="Curator"
                onSortChange={onSortChange}
              />
            </TableRow>
          </TableHead>
          <TableBody>
            {errorMessage ? (
              <TableRow>
                <TableCell colSpan={9} sx={{ py: 4 }}>
                  <Alert
                    action={
                      <Button color="inherit" onClick={onRetry} size="small">
                        Retry
                      </Button>
                    }
                    severity="error"
                  >
                    {errorMessage}
                  </Alert>
                </TableCell>
              </TableRow>
            ) : sessions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} sx={{ py: 6 }}>
                  <Stack spacing={1.5} alignItems="center">
                    <Typography variant="h6">
                      {isLoading ? 'Loading inventory...' : 'No curation sessions match these filters.'}
                    </Typography>
                    <Typography color="text.secondary" variant="body2">
                      {hasActiveFilters
                        ? 'Try clearing one or more filters to broaden the queue.'
                        : 'Prepared sessions will appear here once they are ready for review.'}
                    </Typography>
                    {hasActiveFilters && !isLoading && (
                      <Button onClick={onClearFilters} variant="outlined">
                        Clear filters
                      </Button>
                    )}
                  </Stack>
                </TableCell>
              </TableRow>
            ) : (
              sessions.map((session) => {
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
                    <TableCell sx={{ maxWidth: 340 }}>
                      <Stack spacing={0.5}>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {session.document.title}
                        </Typography>
                        <Typography color="text.secondary" variant="caption">
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
                          backgroundColor: alpha(
                            theme.palette[getAdapterChipColor(session.adapter)].main,
                            0.14
                          ),
                          borderColor: alpha(
                            theme.palette[getAdapterChipColor(session.adapter)].main,
                            0.28
                          ),
                          color: theme.palette[getAdapterChipColor(session.adapter)].light,
                        }}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      <Stack spacing={0.35}>
                        <Typography variant="body2">
                          {session.progress.total_candidates} total
                        </Typography>
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
                      <Typography variant="body2">
                        {formatSessionDate(session.prepared_at)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Stack spacing={0.35}>
                        <Typography variant="body2">
                          {formatLastWorkedAt(session.last_worked_at)}
                        </Typography>
                        {session.last_worked_at && (
                          <Typography color="text.secondary" variant="caption">
                            {formatSessionDate(session.last_worked_at)}
                          </Typography>
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {renderCuratorName(session)}
                      </Typography>
                    </TableCell>
                  </TableRow>
                )
              })
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
            {renderRangeLabel(pageInfo)}
          </Typography>
          {isRefreshing && !isLoading && (
            <Typography color="text.secondary" variant="caption">
              Updating...
            </Typography>
          )}
        </Stack>

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
      </Stack>
    </Paper>
  )
}
