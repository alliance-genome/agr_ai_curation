import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Typography } from '@mui/material'

export interface SelfExclusionDialogProps {
  open: boolean
  allowedGroupIds: string[]
  currentUserGroupIds: string[]
  onConfirm: () => void
  onCancel: () => void
}

export function SelfExclusionDialog({ open, allowedGroupIds, currentUserGroupIds, onConfirm, onCancel }: SelfExclusionDialogProps) {
  return (
    <Dialog open={open} onClose={onCancel} maxWidth="xs" fullWidth aria-labelledby="self-exclusion-title">
      <DialogTitle id="self-exclusion-title">Save a restriction that excludes you?</DialogTitle>
      <DialogContent>
        <Alert severity="warning">
          Available to groups is set to {allowedGroupIds.join(', ')}, but your current groups are
          {' '}{currentUserGroupIds.join(', ')}. After saving, server authorization may prevent you from using
          this agent.
        </Alert>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} size="small" autoFocus>Go back</Button>
        <Button variant="contained" color="warning" size="small" onClick={onConfirm}>
          Save restriction
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export interface DeleteAgentDialogProps {
  open: boolean
  agentName: string
  saving: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function DeleteAgentDialog({ open, agentName, saving, onConfirm, onCancel }: DeleteAgentDialogProps) {
  return (
    <Dialog open={open} onClose={saving ? undefined : onCancel} maxWidth="xs" fullWidth aria-labelledby="delete-agent-title">
      <DialogTitle id="delete-agent-title">Delete agent?</DialogTitle>
      <DialogContent>
        <Typography sx={{ fontSize: 13, color: 'text.secondary' }}>
          This archives &ldquo;{agentName}&rdquo; so it is no longer available for new use. Saved versions and their history are retained; existing references are not silently retargeted.
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} disabled={saving} size="small" autoFocus>
          Cancel
        </Button>
        <Button onClick={onConfirm} color="error" variant="contained" disabled={saving} size="small">
          {saving ? 'Deleting…' : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export interface UnsavedChangesDialogProps {
  open: boolean
  onDiscard: () => void
  onKeepEditing: () => void
}

export function UnsavedChangesDialog({ open, onDiscard, onKeepEditing }: UnsavedChangesDialogProps) {
  return (
    <Dialog open={open} onClose={onKeepEditing} maxWidth="xs" fullWidth aria-labelledby="unsaved-changes-title">
      <DialogTitle id="unsaved-changes-title">Discard unsaved changes?</DialogTitle>
      <DialogContent>
        <Typography sx={{ fontSize: 13, color: 'text.secondary' }}>
          This draft has edits that are not saved. Leaving now discards them.
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onKeepEditing} size="small" autoFocus>
          Keep editing
        </Button>
        <Button onClick={onDiscard} color="error" variant="contained" size="small">
          Discard
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export interface RevertVersionDialogProps {
  open: boolean
  version: number | null
  saving: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function RevertVersionDialog({ open, version, saving, onConfirm, onCancel }: RevertVersionDialogProps) {
  return (
    <Dialog open={open} onClose={saving ? undefined : onCancel} maxWidth="xs" fullWidth aria-labelledby="revert-version-title">
      <DialogTitle id="revert-version-title">Restore configuration {version}?</DialogTitle>
      <DialogContent>
        <Typography sx={{ fontSize: 13, color: 'text.secondary' }}>
          This saves a new version with the model settings, prompts, tools, group rules,
          access restrictions, and output structure from configuration {version}.
          The agent name and description stay unchanged. Nothing is deleted.
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} disabled={saving} size="small" autoFocus>
          Cancel
        </Button>
        <Button onClick={onConfirm} variant="contained" disabled={saving} size="small">
          {saving ? 'Restoring…' : 'Restore'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
