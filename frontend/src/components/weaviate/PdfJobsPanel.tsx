import React from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Collapse,
  CircularProgress,
  FormControl,
  InputLabel,
  LinearProgress,
  MenuItem,
  Pagination,
  Paper,
  Select,
  SelectChangeEvent,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { ExpandLess, ExpandMore } from '@mui/icons-material';
import type { PdfProcessingJob } from '../../services/weaviate';

interface PdfJobsPanelProps {
  jobs: PdfProcessingJob[];
  loading?: boolean;
  onCancelJob?: (jobId: string) => Promise<void>;
}

const statusColor = (status: PdfProcessingJob['status']): 'default' | 'primary' | 'success' | 'warning' | 'error' => {
  switch (status) {
    case 'running':
      return 'primary';
    case 'cancel_requested':
      return 'warning';
    case 'completed':
      return 'success';
    case 'failed':
      return 'error';
    case 'cancelled':
      return 'default';
    default:
      return 'default';
  }
};

const labelForStatus = (status: PdfProcessingJob['status']): string => {
  switch (status) {
    case 'cancel_requested':
      return 'Cancel Requested';
    case 'cancelled':
      return 'Cancelled';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    default:
      return 'Pending';
  }
};

const canCancel = (job: PdfProcessingJob): boolean => {
  return job.status === 'pending' || job.status === 'running';
};

const canDismiss = (job: PdfProcessingJob): boolean => {
  return job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled';
};

const PdfJobsPanel: React.FC<PdfJobsPanelProps> = ({ jobs, loading = false, onCancelJob }) => {
  const [dismissedJobIds, setDismissedJobIds] = React.useState<Set<string>>(new Set());
  const [page, setPage] = React.useState(1);
  const [rowsPerPage, setRowsPerPage] = React.useState(5);
  const visibleJobs = React.useMemo(
    () => jobs.filter((job) => !dismissedJobIds.has(job.job_id)),
    [dismissedJobIds, jobs]
  );
  const hasJobs = visibleJobs.length > 0;
  const activeCount = visibleJobs.filter((job) => ['pending', 'running', 'cancel_requested'].includes(job.status)).length;
  const hiddenCount = jobs.length - visibleJobs.length;
  const [expanded, setExpanded] = React.useState(() => loading || activeCount > 0);
  const totalPages = Math.max(1, Math.ceil(visibleJobs.length / rowsPerPage));

  React.useEffect(() => {
    setDismissedJobIds((previous) => {
      if (previous.size === 0) {
        return previous;
      }

      const validJobIds = new Set(jobs.map((job) => job.job_id));
      const next = new Set(Array.from(previous).filter((jobId) => validJobIds.has(jobId)));
      return next.size === previous.size ? previous : next;
    });
  }, [jobs]);

  React.useEffect(() => {
    setPage((previousPage) => Math.min(previousPage, totalPages));
  }, [totalPages]);

  React.useEffect(() => {
    if (loading || activeCount > 0) {
      setExpanded(true);
    }
  }, [activeCount, loading]);

  const pageStart = (page - 1) * rowsPerPage;
  const pageEnd = pageStart + rowsPerPage;
  const pagedJobs = visibleJobs.slice(pageStart, pageEnd);

  const handleRowsPerPageChange = (event: SelectChangeEvent<number>) => {
    const nextRows = Number(event.target.value) || 5;
    setRowsPerPage(nextRows);
    setPage(1);
  };

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 2,
        mb: 2,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="h6">PDF Jobs</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip size="small" color={activeCount > 0 ? 'primary' : 'default'} label={`${activeCount} active`} />
          {hiddenCount > 0 && (
            <Button
              size="small"
              onClick={() => setDismissedJobIds(new Set())}
              aria-label={`Show ${hiddenCount} hidden PDF jobs`}
            >
              Show Hidden ({hiddenCount})
            </Button>
          )}
          <Button
            size="small"
            onClick={() => setExpanded((previousExpanded) => !previousExpanded)}
            aria-label={expanded ? 'Collapse PDF jobs' : 'Expand PDF jobs'}
            endIcon={expanded ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
          >
            {expanded ? 'Hide' : 'Show'}
          </Button>
        </Stack>
      </Stack>

      {!expanded && (
        <Typography variant="caption" color="text.secondary">
          Panel collapsed
        </Typography>
      )}

      <Collapse in={expanded} timeout="auto" unmountOnExit>
        {loading && (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ py: 1, minHeight: 24 }}>
            <CircularProgress size={16} />
            <Typography variant="body2" color="text.secondary">
              Loading jobs...
            </Typography>
          </Stack>
        )}

        {!loading && !hasJobs && (
          <Alert severity="info" sx={{ mt: 1 }}>
            No PDF jobs in the last 7 days.
          </Alert>
        )}

        <Stack
          spacing={1.5}
          sx={{
            mt: hasJobs ? 1 : 0,
            minHeight: hasJobs ? 120 : 0,
            maxHeight: 340,
            overflowY: 'auto',
            pr: 0.5,
          }}
        >
          {pagedJobs.map((job) => {
            const progress = Math.max(0, Math.min(100, job.progress_percentage ?? 0));
            const message = job.error_message || job.message || 'Processing...';
            const updatedLabel = job.updated_at ? new Date(job.updated_at).toLocaleString() : 'unknown';

            return (
              <Box key={job.job_id} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 1.5 }}>
                <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600} noWrap>
                      {job.filename || job.document_id}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" noWrap>
                      Job {job.job_id}
                    </Typography>
                  </Box>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Chip label={labelForStatus(job.status)} size="small" color={statusColor(job.status)} />
                    {onCancelJob && (
                      <Tooltip title={canCancel(job) ? 'Cancel job' : 'Cancellation unavailable'}>
                        <span>
                          <Button
                            size="small"
                            variant={canCancel(job) ? 'contained' : 'outlined'}
                            color={canCancel(job) ? 'error' : 'inherit'}
                            disabled={!canCancel(job)}
                            onClick={() => {
                              void onCancelJob(job.job_id);
                            }}
                            sx={{ minWidth: 86 }}
                          >
                            Cancel
                          </Button>
                        </span>
                      </Tooltip>
                    )}
                    {canDismiss(job) && (
                      <Tooltip title="Hide from this list">
                        <Button
                          size="small"
                          variant="outlined"
                          color="inherit"
                          onClick={() => {
                            setDismissedJobIds((previous) => {
                              const next = new Set(previous);
                              next.add(job.job_id);
                              return next;
                            });
                          }}
                          sx={{ minWidth: 86 }}
                        >
                          Hide
                        </Button>
                      </Tooltip>
                    )}
                  </Stack>
                </Stack>

                <LinearProgress
                  variant="determinate"
                  value={progress}
                  sx={{ mt: 1, height: 6, borderRadius: 3 }}
                />
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  {progress}% • {message}
                </Typography>
                <Typography variant="caption" color="text.secondary" noWrap>
                  Updated: {updatedLabel}
                </Typography>
              </Box>
            );
          })}
        </Stack>

        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1.5}
          alignItems={{ xs: 'stretch', sm: 'center' }}
          justifyContent="space-between"
          sx={{ mt: 2, minHeight: 44 }}
        >
          <Typography variant="caption" color="text.secondary">
            {hasJobs
              ? `Showing ${pageStart + 1}-${Math.min(pageEnd, visibleJobs.length)} of ${visibleJobs.length}`
              : 'No jobs to display'}
          </Typography>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <FormControl size="small" sx={{ minWidth: 110 }} disabled={!hasJobs}>
              <InputLabel id="pdf-jobs-rows-per-page-label">Rows</InputLabel>
              <Select<number>
                labelId="pdf-jobs-rows-per-page-label"
                label="Rows"
                value={rowsPerPage}
                onChange={handleRowsPerPageChange}
              >
                <MenuItem value={5}>5 / page</MenuItem>
                <MenuItem value={10}>10 / page</MenuItem>
                <MenuItem value={20}>20 / page</MenuItem>
              </Select>
            </FormControl>
            <Pagination
              count={totalPages}
              page={page}
              onChange={(_event, nextPage) => setPage(nextPage)}
              size="small"
              color="primary"
              disabled={!hasJobs}
            />
          </Stack>
        </Stack>
      </Collapse>
    </Paper>
  );
};

export default PdfJobsPanel;
