import { Box, Chip, Stack, Typography } from '@mui/material'

import { UNNAMED_FILE_LABEL } from '@/components/pdfViewer/viewerFiles'
import {
  hasWaitingSupplements,
  parseSupplementJobProgress,
  type SupplementJobProgress,
  type SupplementProgressEntry,
  type SupplementState,
} from '@/features/documents/supplementProgress'

import {
  SUPPLEMENT_STATE_TEXT,
  formatSupplementWaitEndTime,
  supplementOutcomeReasonText,
  supplementWaitEndsText,
} from './paperFileReasonText'

export interface SupplementFilesListProps {
  /** Parsed I1 supplement progress. When omitted, `metadata` is parsed defensively. */
  progress?: SupplementJobProgress | null
  /** Raw `job.metadata` convenience prop used when `progress` is omitted. */
  metadata?: Record<string, unknown> | null
  /** Optional section heading (for example N37 "Adding files" in the Library details dialog). */
  heading?: string
  /** Compact typography and spacing for dense job cards such as `PdfJobsPanel`. */
  compact?: boolean
  /** Accessible label for the rendered list element. */
  ariaLabel?: string
}

function stateChipColor(
  state: SupplementState,
): 'default' | 'primary' | 'success' | 'warning' | 'info' {
  switch (state) {
    case 'added':
      return 'success'
    case 'adding':
      return 'primary'
    case 'waiting':
      return 'info'
    case 'left_out':
      return 'warning'
    case 'queued':
    default:
      return 'default'
  }
}

function SupplementFileRow({
  entry,
  compact,
}: {
  entry: SupplementProgressEntry
  compact: boolean
}) {
  const displayName = entry.display_name || UNNAMED_FILE_LABEL
  const textVariant = compact ? 'caption' : 'body2'

  return (
    <Box component="li" sx={{ py: compact ? 0.25 : 0.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography variant={textVariant} sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
          {displayName}
        </Typography>
        <Chip
          size="small"
          label={SUPPLEMENT_STATE_TEXT[entry.state]}
          color={stateChipColor(entry.state)}
          variant={entry.state === 'queued' ? 'outlined' : 'filled'}
        />
      </Box>
      {entry.state === 'left_out' ? (
        <Typography variant={textVariant} color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
          {supplementOutcomeReasonText(entry.reason_code, { limitValue: entry.limit_value })}
        </Typography>
      ) : null}
    </Box>
  )
}

/**
 * Shared per-file supplement progress list (interface I1) used by `PdfJobsPanel` and
 * `DocumentFilesSection`. Returns `null` when no supplement entries exist so screens never
 * render an empty "0 files" section.
 */
export default function SupplementFilesList({
  progress,
  metadata,
  heading,
  compact = false,
  ariaLabel = 'Supplement files',
}: SupplementFilesListProps) {
  const resolved = progress !== undefined ? progress : parseSupplementJobProgress(metadata)
  if (!resolved || resolved.supplements.length === 0) {
    return null
  }

  const waitEndTime =
    hasWaitingSupplements(resolved) && resolved.supplement_wait_ends_at
      ? formatSupplementWaitEndTime(resolved.supplement_wait_ends_at)
      : null

  return (
    <Stack spacing={compact ? 0.5 : 0.75} sx={{ mt: compact ? 0.75 : 0 }}>
      {heading ? (
        <Typography variant="subtitle2" gutterBottom={!compact}>
          {heading}
        </Typography>
      ) : null}
      {waitEndTime ? (
        <Typography variant={compact ? 'caption' : 'body2'} color="text.secondary">
          {supplementWaitEndsText(waitEndTime)}
        </Typography>
      ) : null}
      <Box component="ul" aria-label={ariaLabel} sx={{ m: 0, pl: compact ? 2 : 2.5 }}>
        {resolved.supplements.map((entry, index) => (
          <SupplementFileRow
            key={`${entry.provider_file_id}-${index}`}
            entry={entry}
            compact={compact}
          />
        ))}
      </Box>
    </Stack>
  )
}
