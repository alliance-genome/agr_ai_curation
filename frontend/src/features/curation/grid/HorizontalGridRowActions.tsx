import { Button, Stack, Typography } from '@mui/material'

import type { CurationCandidate } from '@/features/curation/types'
import { isValidatedPendingCandidate } from '@/features/curation/workspace/workPaneToolbar'

export interface HorizontalGridRowActionsProps {
  candidate: CurationCandidate
  isDeciding: boolean
  onAccept: () => void
  onReject: () => void
}

export default function HorizontalGridRowActions({
  candidate,
  isDeciding,
  onAccept,
  onReject,
}: HorizontalGridRowActionsProps) {
  if (candidate.status !== 'pending') {
    return (
      <Typography color="text.secondary" textTransform="capitalize" variant="caption">
        {candidate.status}
      </Typography>
    )
  }

  const isBusy = isDeciding
  const label = candidate.display_label ?? candidate.candidate_id

  return (
    <Stack alignItems="stretch" spacing={0.5}>
      <Button
        aria-label={`Accept ${label}`}
        color="success"
        disabled={isBusy || !isValidatedPendingCandidate(candidate)}
        onClick={onAccept}
        size="small"
        sx={{ fontSize: '0.68rem', minWidth: 0, textTransform: 'none' }}
        variant="outlined"
      >
        Accept
      </Button>
      <Button
        aria-label={`Reject ${label}`}
        color="error"
        disabled={isBusy}
        onClick={onReject}
        size="small"
        sx={{ fontSize: '0.68rem', minWidth: 0, textTransform: 'none' }}
        variant="text"
      >
        Reject
      </Button>
    </Stack>
  )
}
