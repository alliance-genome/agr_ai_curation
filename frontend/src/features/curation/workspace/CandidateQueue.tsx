import { useMemo } from 'react'

import {
  Box,
  ButtonBase,
  Stack,
  Typography,
} from '@mui/material'
import { purple } from '@mui/material/colors'
import { alpha, type Theme, useTheme } from '@mui/material/styles'

import { getValidationBuckets } from '@/features/curation/inventory/inventoryPresentation'
import type {
  CurationCandidate,
  CurationSessionProgress,
  CurationValidationSummary,
} from '@/features/curation/types'

import { useCurationWorkspaceContext } from './CurationWorkspaceContext'

type CandidateQueueCardState = CurationCandidate['status'] | 'active'

interface CandidateQueueHeaderCounts {
  accepted: number
  editing: number
  pending: number
}

function formatCandidateLabel(candidate: CurationCandidate): string {
  return candidate.display_label ?? candidate.candidate_id
}

function getValidationBadgeLabel(validation?: CurationValidationSummary | null): string {
  if (!validation) {
    return 'No validation'
  }

  if (validation.state === 'pending') {
    return 'Validating'
  }

  if (validation.state === 'failed') {
    return 'Validation failed'
  }

  const buckets = getValidationBuckets(validation)

  if (buckets.total === 0) {
    return 'No results'
  }

  const parts = [`${buckets.success}/${buckets.total} ✓`]

  if (buckets.warning > 0) {
    parts.push(`${buckets.warning}⚠`)
  }

  if (buckets.error > 0) {
    parts.push(`${buckets.error}✕`)
  }

  return parts.join(' ')
}

function getCardState(
  candidate: CurationCandidate,
  activeCandidateId: string | null,
): CandidateQueueCardState {
  if (candidate.candidate_id === activeCandidateId) {
    return 'active'
  }

  return candidate.status
}

function getHeaderCounts(
  progress: CurationSessionProgress,
  activeCandidate: CurationCandidate | null,
): CandidateQueueHeaderCounts {
  const activeStatus = activeCandidate?.status ?? null

  return {
    accepted: Math.max(0, progress.accepted_candidates - (activeStatus === 'accepted' ? 1 : 0)),
    editing: activeCandidate ? 1 : 0,
    pending: Math.max(0, progress.pending_candidates - (activeStatus === 'pending' ? 1 : 0)),
  }
}

function getCardTone(
  theme: Theme,
  state: CandidateQueueCardState,
): {
  borderColor: string
  backgroundColor: string
  iconColor: string
  boxShadow: string
  opacity: number
} {
  switch (state) {
    case 'accepted':
      return {
        borderColor: theme.palette.success.main,
        backgroundColor: alpha(theme.palette.success.main, 0.14),
        iconColor: theme.palette.success.light,
        boxShadow: 'none',
        opacity: 1,
      }
    case 'rejected':
      return {
        borderColor: theme.palette.error.main,
        backgroundColor: alpha(theme.palette.error.main, 0.12),
        iconColor: theme.palette.error.light,
        boxShadow: 'none',
        opacity: 0.72,
      }
    case 'pending':
      return {
        borderColor: alpha(theme.palette.text.secondary, 0.45),
        backgroundColor: alpha(theme.palette.common.white, 0.03),
        iconColor: theme.palette.text.secondary,
        boxShadow: 'none',
        opacity: 0.78,
      }
    case 'active':
    default:
      return {
        borderColor: purple[300],
        backgroundColor: alpha(purple[300], 0.18),
        iconColor: purple[100],
        boxShadow: `0 0 0 1px ${alpha(purple[200], 0.35)}, 0 12px 24px ${alpha(purple[400], 0.18)}`,
        opacity: 1,
      }
  }
}

function getCardStatusIcon(state: CandidateQueueCardState): string {
  switch (state) {
    case 'accepted':
      return '✓'
    case 'rejected':
      return '✕'
    case 'active':
      return '▸'
    case 'pending':
    default:
      return '—'
  }
}

function getProgressSegmentColor(
  theme: Theme,
  candidate: CurationCandidate,
  activeCandidateId: string | null,
): string {
  if (candidate.candidate_id === activeCandidateId) {
    return theme.palette.warning.main
  }

  if (candidate.status === 'pending') {
    return alpha(theme.palette.text.secondary, 0.24)
  }

  return theme.palette.success.main
}

function SummaryBadge({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <Box
      sx={(theme) => ({
        px: 0.75,
        py: 0.4,
        borderRadius: 999,
        border: `1px solid ${alpha(theme.palette.divider, 0.9)}`,
        backgroundColor: alpha(theme.palette.background.default, 0.32),
      })}
    >
      <Typography color="text.secondary" variant="caption">
        {value}
        {' '}
        {label}
      </Typography>
    </Box>
  )
}

export default function CandidateQueue() {
  const theme = useTheme()
  const {
    activeCandidate,
    activeCandidateId,
    candidates,
    session,
    setActiveCandidate,
  } = useCurationWorkspaceContext()

  const orderedCandidates = useMemo(
    () => [...candidates].sort((left, right) => left.order - right.order),
    [candidates],
  )

  const headerCounts = useMemo(
    () => getHeaderCounts(session.progress, activeCandidate),
    [activeCandidate, session.progress],
  )

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Stack
        spacing={1.25}
        sx={{
          px: 1.25,
          pt: 1.25,
          pb: 1,
          borderBottom: `1px solid ${alpha(theme.palette.divider, 0.85)}`,
        }}
      >
        <Stack alignItems="center" direction="row" justifyContent="space-between" spacing={1}>
          <Typography variant="subtitle2">
            Candidates ({orderedCandidates.length})
          </Typography>
          <Typography color="text.secondary" variant="caption">
            {session.progress.reviewed_candidates}
            /
            {session.progress.total_candidates}
            {' '}
            reviewed
          </Typography>
        </Stack>

        <Stack direction="row" flexWrap="wrap" spacing={0.75} useFlexGap>
          <SummaryBadge label="accepted" value={`${headerCounts.accepted}✓`} />
          <SummaryBadge label="editing" value={`${headerCounts.editing}✎`} />
          <SummaryBadge label="pending" value={`${headerCounts.pending}—`} />
        </Stack>

        <Stack
          aria-label="Candidate queue progress"
          data-testid="candidate-queue-progress"
          direction="row"
          spacing={0.5}
          sx={{ minHeight: 6 }}
        >
          {orderedCandidates.map((candidate) => (
            <Box
              key={candidate.candidate_id}
              data-segment-state={
                candidate.candidate_id === activeCandidateId
                  ? 'active'
                  : candidate.status === 'pending'
                    ? 'todo'
                    : 'done'
              }
              data-testid={`candidate-queue-progress-segment-${candidate.candidate_id}`}
              sx={{
                flex: 1,
                height: 6,
                borderRadius: 999,
                backgroundColor: getProgressSegmentColor(theme, candidate, activeCandidateId),
              }}
            />
          ))}
        </Stack>
      </Stack>

      <Stack
        component="ul"
        spacing={1}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          listStyle: 'none',
          m: 0,
          p: 1.25,
        }}
      >
        {orderedCandidates.map((candidate) => {
          const cardState = getCardState(candidate, activeCandidateId)
          const tone = getCardTone(theme, cardState)

          return (
            <Box component="li" key={candidate.candidate_id} sx={{ minWidth: 0 }}>
              <ButtonBase
                aria-pressed={candidate.candidate_id === activeCandidateId}
                data-status-appearance={cardState}
                data-testid={`candidate-queue-card-${candidate.candidate_id}`}
                onClick={() => setActiveCandidate(candidate.candidate_id)}
                sx={{
                  width: '100%',
                  display: 'block',
                  textAlign: 'left',
                  borderRadius: 2,
                }}
              >
                <Box
                  sx={{
                    borderLeft: `3px solid ${tone.borderColor}`,
                    borderRadius: 2,
                    backgroundColor: tone.backgroundColor,
                    boxShadow: tone.boxShadow,
                    opacity: tone.opacity,
                    px: 1.1,
                    py: 1,
                    transition: 'background-color 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease',
                  }}
                >
                  <Stack spacing={0.75}>
                    <Stack alignItems="flex-start" direction="row" spacing={1}>
                      <Typography
                        aria-hidden="true"
                        data-testid={`candidate-queue-status-icon-${candidate.candidate_id}`}
                        sx={{
                          color: tone.iconColor,
                          fontSize: '1rem',
                          lineHeight: 1.3,
                        }}
                        variant="body2"
                      >
                        {getCardStatusIcon(cardState)}
                      </Typography>

                      <Box sx={{ minWidth: 0, flex: 1 }}>
                        <Typography
                          sx={{
                            display: '-webkit-box',
                            overflow: 'hidden',
                            WebkitBoxOrient: 'vertical',
                            WebkitLineClamp: 2,
                            wordBreak: 'break-word',
                          }}
                          variant="body2"
                          fontWeight={cardState === 'active' ? 700 : 600}
                        >
                          {formatCandidateLabel(candidate)}
                        </Typography>
                        {candidate.secondary_label ? (
                          <Typography
                            color="text.secondary"
                            sx={{
                              mt: 0.25,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                            variant="caption"
                          >
                            {candidate.secondary_label}
                          </Typography>
                        ) : null}
                      </Box>
                    </Stack>

                    <Stack direction="row" flexWrap="wrap" spacing={0.75} useFlexGap>
                      <Box
                        sx={{
                          px: 0.6,
                          py: 0.3,
                          borderRadius: 999,
                          backgroundColor: alpha(theme.palette.background.default, 0.32),
                        }}
                      >
                        <Typography
                          data-testid={`candidate-queue-validation-${candidate.candidate_id}`}
                          variant="caption"
                        >
                          {getValidationBadgeLabel(candidate.validation)}
                        </Typography>
                      </Box>

                      <Typography
                        color="text.secondary"
                        data-testid={`candidate-queue-evidence-${candidate.candidate_id}`}
                        variant="caption"
                      >
                        📎 {candidate.evidence_anchors.length}
                      </Typography>
                    </Stack>
                  </Stack>
                </Box>
              </ButtonBase>
            </Box>
          )
        })}

        {orderedCandidates.length === 0 ? (
          <Typography color="text.secondary" component="li" variant="body2">
            No candidates available for this session.
          </Typography>
        ) : null}
      </Stack>
    </Box>
  )
}
