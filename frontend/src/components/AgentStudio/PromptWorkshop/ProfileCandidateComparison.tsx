import { Alert, Box, Button, Chip, Stack, Typography } from '@mui/material'
import type { GenericProfileContract } from '@/services/genericProfileService'
import { compareProfileDrafts } from './profileEditorModel'

export type ProfileCandidateStatus = 'proposed' | 'applied' | 'canceled' | 'stale' | 'undone'
export interface ProfileCandidateComparisonProps {
  before: GenericProfileContract | null
  candidate: GenericProfileContract
  origin: string
  status: ProfileCandidateStatus
  busy?: boolean
  onApply?: () => void
  onCancel?: () => void
  onUndo?: () => void
}

const STATUS_LABELS: Record<ProfileCandidateStatus, string> = {
  proposed: 'Proposed changes — not applied', applied: 'Applied to the unsaved draft',
  canceled: 'Proposal canceled', stale: 'Proposal is stale — refresh before applying',
  undone: 'Proposal undone',
}

function changeValue(value: unknown): string {
  if (typeof value === 'string') return value || '(empty)'
  if (value === undefined) return '(absent)'
  return JSON.stringify(value, null, 2)
}

/** Presentation only. ALL-1051 owns fingerprint checks, mutation and undo. */
export default function ProfileCandidateComparison({ before, candidate, origin, status, busy = false, onApply, onCancel, onUndo }: ProfileCandidateComparisonProps) {
  const changes = compareProfileDrafts(before, candidate)
  return <Stack spacing={1.5} component="section" aria-label="Output Structure candidate comparison">
    <Typography variant="subtitle1">Review proposed Output Structure</Typography>
    <Typography variant="body2">Source: {origin}</Typography>
    <Alert severity={status === 'stale' ? 'warning' : 'info'} role="status">{STATUS_LABELS[status]}. Saving a revision remains a separate explicit action.</Alert>
    {changes.length === 0 ? <Typography>No changes to the Output Structure.</Typography> : null}
    <Box component="ul" sx={{ listStyle: 'none', p: 0, m: 0 }}>
      {changes.map((change, index) => <Box component="li" key={index} sx={{ borderBottom: 1, borderColor: 'divider', py: 1 }}>
        <Stack direction="row" gap={1} alignItems="center"><Chip size="small" label={change.kind} />
          <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{change.path || 'Entire structure'}</Typography></Stack>
        {change.kind !== 'added' ? <Typography component="pre" variant="body2" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>Before: {changeValue(change.before)}</Typography> : null}
        {change.kind !== 'removed' ? <Typography component="pre" variant="body2" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>After: {changeValue(change.after)}</Typography> : null}
      </Box>)}
    </Box>
    <Stack direction="row" gap={1} useFlexGap flexWrap="wrap">
      {onApply ? <Button disabled={busy || status !== 'proposed'} onClick={onApply}>Apply to draft</Button> : null}
      {onCancel ? <Button disabled={busy || !['proposed', 'stale'].includes(status)} onClick={onCancel}>Cancel proposal</Button> : null}
      {onUndo ? <Button disabled={busy || status !== 'applied'} onClick={onUndo}>Undo applied changes</Button> : null}
    </Stack>
  </Stack>
}
