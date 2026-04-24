import type { ChipProps, Theme } from '@mui/material'
import { alpha } from '@mui/material/styles'
import { format, formatDistanceToNow } from 'date-fns'

import type {
  CurationAdapterRef,
  CurationEvidenceSummary,
  CurationSessionStats,
  CurationSessionStatus,
  CurationValidationSummary,
} from '../types'

export interface InventoryFilterOption {
  key: string
  label: string
  colorToken?: string | null
}

interface ValidationBuckets {
  success: number
  warning: number
  error: number
  neutral: number
  total: number
}

const STATUS_LABELS: Record<CurationSessionStatus, string> = {
  new: 'New',
  in_progress: 'In Progress',
  paused: 'Paused',
  ready_for_submission: 'Ready',
  submitted: 'Submitted',
  rejected: 'Rejected',
}

export const STATUS_FILTER_ORDER: CurationSessionStatus[] = [
  'new',
  'in_progress',
  'ready_for_submission',
  'submitted',
  'paused',
  'rejected',
]

export function getStatusLabel(status: CurationSessionStatus): string {
  return STATUS_LABELS[status]
}

export function getStatusCount(
  status: CurationSessionStatus,
  stats?: CurationSessionStats
): number | undefined {
  if (!stats) {
    return undefined
  }

  switch (status) {
    case 'new':
      return stats.new_sessions
    case 'in_progress':
      return stats.in_progress_sessions
    case 'ready_for_submission':
      return stats.ready_for_submission_sessions
    case 'submitted':
      return stats.submitted_sessions
    case 'paused':
      return stats.paused_sessions
    case 'rejected':
      return stats.rejected_sessions
    default:
      return undefined
  }
}

export function getStatusChipColor(status: CurationSessionStatus): NonNullable<ChipProps['color']> {
  switch (status) {
    case 'new':
      return 'primary'
    case 'in_progress':
      return 'warning'
    case 'ready_for_submission':
      return 'info'
    case 'submitted':
      return 'success'
    case 'rejected':
      return 'error'
    case 'paused':
    default:
      return 'default'
  }
}

export function getAdapterLabel(adapter: CurationAdapterRef): string {
  return adapter.display_label || adapter.adapter_key
}

export function getAdapterChipColor(
  adapter: Pick<CurationAdapterRef, 'color_token' | 'metadata'>
): 'primary' | 'secondary' | 'info' | 'success' | 'warning' | 'error' {
  const metadataColorToken =
    typeof adapter.metadata?.color_token === 'string'
      ? adapter.metadata.color_token
      : null
  const normalizedToken = (adapter.color_token || metadataColorToken || '').trim().toLowerCase()

  switch (normalizedToken) {
    case 'purple':
    case 'indigo':
      return 'secondary'
    case 'cyan':
    case 'teal':
    case 'sky':
      return 'info'
    case 'green':
    case 'emerald':
      return 'success'
    case 'yellow':
    case 'amber':
    case 'orange':
      return 'warning'
    case 'red':
    case 'rose':
      return 'error'
    case 'blue':
      return 'primary'
    default:
      return 'primary'
  }
}

export function formatSessionDate(value?: string | null): string {
  if (!value) {
    return 'Not available'
  }

  return format(new Date(value), 'MMM d, yyyy')
}

export function formatLastWorkedAt(value?: string | null): string {
  if (!value) {
    return 'Not started'
  }

  return formatDistanceToNow(new Date(value), { addSuffix: true })
}

export function getValidationBuckets(
  validation?: CurationValidationSummary | null
): ValidationBuckets {
  if (!validation) {
    return {
      success: 0,
      warning: 0,
      error: 0,
      neutral: 0,
      total: 0,
    }
  }

  const success = validation.counts.validated
  const warning = validation.counts.ambiguous + validation.counts.conflict
  const error = validation.counts.not_found + validation.counts.invalid_format
  const neutral = validation.counts.skipped + validation.counts.overridden

  return {
    success,
    warning,
    error,
    neutral,
    total: success + warning + error + neutral,
  }
}

export function getValidationLabel(validation?: CurationValidationSummary | null): string {
  if (!validation) {
    return 'Not requested'
  }

  if (validation.state === 'pending') {
    return 'Pending'
  }

  if (validation.state === 'failed') {
    return 'Failed'
  }

  const buckets = getValidationBuckets(validation)
  const parts: string[] = []

  if (buckets.success > 0) {
    parts.push(`${buckets.success} valid`)
  }
  if (buckets.warning > 0) {
    parts.push(`${buckets.warning} warn`)
  }
  if (buckets.error > 0) {
    parts.push(`${buckets.error} error`)
  }
  if (buckets.neutral > 0) {
    parts.push(`${buckets.neutral} skipped`)
  }

  return parts.join(' ') || 'No results'
}

export function getEvidenceLabel(evidence?: CurationEvidenceSummary | null): string {
  if (!evidence) {
    return 'No evidence'
  }

  return `${evidence.resolved_anchor_count} / ${evidence.total_anchor_count} resolved`
}

export function getEvidenceTone(
  theme: Theme,
  evidence?: CurationEvidenceSummary | null
): string {
  if (!evidence || evidence.total_anchor_count === 0) {
    return theme.palette.text.secondary
  }

  const resolvedRatio = evidence.resolved_anchor_count / evidence.total_anchor_count

  if (resolvedRatio >= 0.75) {
    return theme.palette.success.main
  }

  if (resolvedRatio > 0) {
    return theme.palette.warning.main
  }

  return evidence.degraded ? theme.palette.error.main : theme.palette.text.secondary
}

export function getValidationSegmentStyles(
  theme: Theme,
  validation?: CurationValidationSummary | null
): Array<{ color: string; flex: number }> {
  const buckets = getValidationBuckets(validation)

  return [
    {
      color: buckets.success > 0
        ? theme.palette.success.main
        : alpha(theme.palette.text.secondary, 0.18),
      flex: buckets.success || 1,
    },
    {
      color: buckets.warning > 0
        ? theme.palette.warning.main
        : alpha(theme.palette.text.secondary, 0.18),
      flex: buckets.warning || 1,
    },
    {
      color: buckets.error > 0
        ? theme.palette.error.main
        : alpha(theme.palette.text.secondary, 0.18),
      flex: buckets.error || 1,
    },
    {
      color: buckets.neutral > 0
        ? theme.palette.grey[500]
        : alpha(theme.palette.text.secondary, 0.18),
      flex: buckets.neutral || 1,
    },
  ]
}
