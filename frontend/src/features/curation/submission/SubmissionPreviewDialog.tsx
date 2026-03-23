import { useEffect, useMemo, useState } from 'react'

import CheckCircleOutlineRoundedIcon from '@mui/icons-material/CheckCircleOutlineRounded'
import DownloadRoundedIcon from '@mui/icons-material/DownloadRounded'
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded'
import PreviewRoundedIcon from '@mui/icons-material/PreviewRounded'
import SendRoundedIcon from '@mui/icons-material/SendRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Paper,
  Stack,
  Typography,
} from '@mui/material'

import { fetchSubmissionPreview } from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationCandidate,
  CurationReviewSession,
  CurationSubmissionPreviewResponse,
  SubmissionMode,
} from '@/features/curation/types'

const DEFAULT_SUBMISSION_TARGET_KEY = 'review_export_bundle'

const MODE_COPY: Record<
  SubmissionMode,
  {
    description: string
    footerLabel: string
    title: string
  }
> = {
  preview: {
    title: 'Preview payload',
    description: 'Inspect the assembled submission payload without side effects.',
    footerLabel: 'Refresh preview',
  },
  export: {
    title: 'Export bundle',
    description: 'Review the generated export bundle and download it when a bundle is available.',
    footerLabel: 'Download bundle',
  },
  direct_submit: {
    title: 'Submission payload',
    description: 'Run final readiness checks before sending this session to a downstream target.',
    footerLabel: 'Submit',
  },
}

interface SubmissionPreviewDialogProps {
  open: boolean
  session: CurationReviewSession
  candidates: CurationCandidate[]
  onClose: () => void
  onSubmit?: (response: CurationSubmissionPreviewResponse) => Promise<void> | void
  submitAvailable?: boolean
}

function countBlockingValidationIssues(
  response: CurationSubmissionPreviewResponse | null,
): number {
  const counts = response?.session_validation?.summary.counts
  if (!counts) {
    return 0
  }

  return counts.ambiguous + counts.not_found + counts.invalid_format + counts.conflict
}

function formatValidationSummary(
  response: CurationSubmissionPreviewResponse | null,
): string | null {
  const counts = response?.session_validation?.summary.counts
  if (!counts) {
    return null
  }

  return [
    `${counts.invalid_format} invalid`,
    `${counts.ambiguous} ambiguous`,
    `${counts.not_found} unresolved`,
    `${counts.conflict} conflicting`,
  ].join(' • ')
}

function payloadPreview(response: CurationSubmissionPreviewResponse | null): string {
  const payload = response?.submission.payload
  if (!payload) {
    return 'No payload was returned for this request.'
  }

  if (payload.payload_text) {
    return payload.payload_text
  }

  if (payload.payload_json === null || payload.payload_json === undefined) {
    return 'No payload was returned for this request.'
  }

  return JSON.stringify(payload.payload_json, null, 2)
}

function downloadPayload(response: CurationSubmissionPreviewResponse) {
  const payload = response.submission.payload
  if (!payload) {
    return
  }

  const payloadBody = payload.payload_text
    ?? JSON.stringify(payload.payload_json ?? {}, null, 2)
  const blob = new Blob([payloadBody], {
    type: payload.content_type ?? 'application/json',
  })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')

  link.href = url
  link.download = payload.filename ?? `curation-${response.submission.session_id}.json`
  link.click()
  window.URL.revokeObjectURL(url)
}

export default function SubmissionPreviewDialog({
  open,
  session,
  candidates,
  onClose,
  onSubmit,
  submitAvailable = false,
}: SubmissionPreviewDialogProps) {
  const [mode, setMode] = useState<SubmissionMode>('preview')
  const [response, setResponse] = useState<CurationSubmissionPreviewResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) {
      return
    }

    setMode('preview')
    setResponse(null)
    setError(null)
    setRefreshNonce(0)
    setSubmitting(false)
  }, [open, session.session_id])

  useEffect(() => {
    if (!open) {
      return
    }

    let cancelled = false

    setLoading(true)
    setError(null)

    void fetchSubmissionPreview({
      session_id: session.session_id,
      mode,
      target_key: DEFAULT_SUBMISSION_TARGET_KEY,
      include_payload: true,
    })
      .then((nextResponse) => {
        if (cancelled) {
          return
        }

        setResponse(nextResponse)
      })
      .catch((fetchError) => {
        if (cancelled) {
          return
        }

        setResponse(null)
        setError(fetchError instanceof Error ? fetchError.message : 'Unable to load submission preview.')
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [mode, open, refreshNonce, session.session_id])

  const candidateLabels = useMemo(
    () => new Map(candidates.map((candidate) => [candidate.candidate_id, candidate.display_label])),
    [candidates],
  )
  const readiness = response?.submission.readiness ?? []
  const readyCount = readiness.filter((item) => item.ready).length
  const blockingValidationIssues = countBlockingValidationIssues(response)
  const validationSummary = formatValidationSummary(response)
  const canDownload = mode === 'export' && !loading && readyCount > 0 && Boolean(response?.submission.payload)
  const canSubmit = Boolean(
    mode === 'direct_submit'
      && submitAvailable
      && onSubmit
      && response
      && readyCount > 0
      && blockingValidationIssues === 0,
  )

  async function handlePrimaryAction() {
    if (mode === 'preview') {
      setRefreshNonce((current) => current + 1)
      return
    }

    if (mode === 'export') {
      if (response && canDownload) {
        downloadPayload(response)
      }
      return
    }

    if (!response || !onSubmit || !canSubmit) {
      return
    }

    setSubmitting(true)
    try {
      await onSubmit(response)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onClose={loading || submitting ? undefined : onClose} fullWidth maxWidth="lg">
      <DialogTitle>Submission preview</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5}>
          <Stack
            direction={{ xs: 'column', md: 'row' }}
            spacing={1.5}
            justifyContent="space-between"
          >
            <Box>
              <Typography variant="body2" color="text.secondary">
                {session.document.title}
              </Typography>
              <Typography variant="h6">
                {MODE_COPY[mode].title}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {MODE_COPY[mode].description}
              </Typography>
            </Box>

            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Button
                onClick={() => setMode('preview')}
                startIcon={<PreviewRoundedIcon />}
                variant={mode === 'preview' ? 'contained' : 'outlined'}
              >
                Preview mode
              </Button>
              <Button
                onClick={() => setMode('export')}
                startIcon={<DownloadRoundedIcon />}
                variant={mode === 'export' ? 'contained' : 'outlined'}
              >
                Export mode
              </Button>
              <Button
                onClick={() => setMode('direct_submit')}
                startIcon={<SendRoundedIcon />}
                variant={mode === 'direct_submit' ? 'contained' : 'outlined'}
              >
                Submit mode
              </Button>
            </Stack>
          </Stack>

          {error ? (
            <Alert severity="error">{error}</Alert>
          ) : null}

          {validationSummary ? (
            <Alert severity={blockingValidationIssues > 0 ? 'warning' : 'info'}>
              Session validation summary: {validationSummary}
            </Alert>
          ) : null}

          {mode === 'export' && !canDownload && !loading ? (
            <Alert severity="warning">
              No export bundle is available until at least one candidate is submission-ready.
            </Alert>
          ) : null}

          {mode === 'direct_submit' && !submitAvailable ? (
            <Alert severity="warning">
              No submission transport is configured for this adapter yet.
            </Alert>
          ) : null}

          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Chip
              color="success"
              label={`${readyCount} ready`}
              size="small"
              variant="outlined"
            />
            <Chip
              color={readiness.length === readyCount ? 'default' : 'warning'}
              label={`${Math.max(readiness.length - readyCount, 0)} blocked`}
              size="small"
              variant="outlined"
            />
            {response?.submission.payload?.filename ? (
              <Chip
                label={response.submission.payload.filename}
                size="small"
                variant="outlined"
              />
            ) : null}
          </Stack>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack spacing={1.5}>
              <Typography variant="subtitle1">Candidate readiness</Typography>
              {loading && !response ? (
                <Stack direction="row" spacing={1} alignItems="center">
                  <CircularProgress size={18} />
                  <Typography variant="body2" color="text.secondary">
                    Building submission preview...
                  </Typography>
                </Stack>
              ) : readiness.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No candidate readiness data is available yet.
                </Typography>
              ) : (
                readiness.map((item) => (
                  <Box key={item.candidate_id}>
                    <Stack
                      direction={{ xs: 'column', sm: 'row' }}
                      spacing={1}
                      justifyContent="space-between"
                    >
                      <Stack direction="row" spacing={1} alignItems="center">
                        {item.ready ? (
                          <CheckCircleOutlineRoundedIcon color="success" fontSize="small" />
                        ) : (
                          <ErrorOutlineRoundedIcon color="warning" fontSize="small" />
                        )}
                        <Typography variant="subtitle2">
                          {candidateLabels.get(item.candidate_id) ?? item.candidate_id}
                        </Typography>
                      </Stack>
                      <Chip
                        color={item.ready ? 'success' : 'warning'}
                        label={item.ready ? 'Ready' : 'Blocked'}
                        size="small"
                        variant={item.ready ? 'outlined' : 'filled'}
                      />
                    </Stack>

                    {item.blocking_reasons.length > 0 ? (
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
                        {item.blocking_reasons.join(' ')}
                      </Typography>
                    ) : null}
                    {item.warnings.length > 0 ? (
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
                        {item.warnings.join(' ')}
                      </Typography>
                    ) : null}
                    <Divider sx={{ mt: 1.5 }} />
                  </Box>
                ))
              )}
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack spacing={1}>
              <Typography variant="subtitle1">Assembled payload</Typography>
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 2,
                  borderRadius: 1.5,
                  bgcolor: 'background.default',
                  overflowX: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontSize: '0.85rem',
                }}
              >
                {loading && !response ? 'Loading payload...' : payloadPreview(response)}
              </Box>
            </Stack>
          </Paper>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading || submitting}>
          Close
        </Button>
        <Button
          onClick={() => void handlePrimaryAction()}
          disabled={
            loading
            || submitting
            || (mode === 'export' && !canDownload)
            || (mode === 'direct_submit' && !canSubmit)
          }
          variant="contained"
        >
          {submitting ? 'Submitting...' : MODE_COPY[mode].footerLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
