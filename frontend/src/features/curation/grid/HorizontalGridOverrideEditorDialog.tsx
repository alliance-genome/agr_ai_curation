import { useId, useState } from 'react'

import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import type { DomainEnvelopeReviewResolvedValue } from '@/features/curation/types'
import {
  horizontalGridIdentityKeyLabel,
  horizontalGridIdentityKeyRequired,
  horizontalGridIdentityKeys,
  horizontalGridOverrideChanged,
  horizontalGridOverrideIdentity,
  horizontalGridOverrideProblem,
  horizontalGridOverrideTargetName,
  horizontalGridOverrideTargetSummary,
  horizontalGridRemovableElement,
  type HorizontalGridOverrideIdentity,
} from './horizontalGridOverride'

export interface HorizontalGridOverrideEditorDialogProps {
  // A rejected save (the backend's own words), shown inline; the editor stays open.
  error: string | null
  // The cell's own field path; values below it are named from their path.
  fieldPath: string
  fieldLabel: string
  isSaving: boolean
  onClose: () => void
  onRemove: (value: DomainEnvelopeReviewResolvedValue) => void
  // Removes one element from a list of validated values.
  onRemoveElement: (value: DomainEnvelopeReviewResolvedValue) => void
  onSave: (value: DomainEnvelopeReviewResolvedValue, identity: HorizontalGridOverrideIdentity) => void
  onSelectValue: () => void
  open: boolean
  // The values this cell can override: one, or each element of a list. They
  // come from the current review row, so a refresh updates their stored state.
  targets: readonly DomainEnvelopeReviewResolvedValue[]
}

function targetLabel(fieldPath: string, fieldLabel: string, value: DomainEnvelopeReviewResolvedValue): string {
  const name = horizontalGridOverrideTargetName(fieldPath, value) ?? fieldLabel
  return `${name}: ${horizontalGridOverrideTargetSummary(value)}`
}

/**
 * A curator validation override: the curator sets every identity key of one
 * value together, in one save (the identifier, the name, and any key only a
 * validator fills, such as a taxon). The paper wording, the validator's result
 * and its words stay as they are, for reference. A list cell picks the element
 * to override first.
 */
export default function HorizontalGridOverrideEditorDialog({
  error,
  fieldPath,
  fieldLabel,
  isSaving,
  onClose,
  onRemove,
  onRemoveElement,
  onSave,
  onSelectValue,
  open,
  targets,
}: HorizontalGridOverrideEditorDialogProps) {
  const titleId = useId()
  const [selectedPath, setSelectedPath] = useState(targets[0]?.value_path ?? '')
  // The curator's entries per value, kept across element switches and refreshes.
  const [entries, setEntries] = useState<Record<string, HorizontalGridOverrideIdentity>>({})
  const [problem, setProblem] = useState<string | null>(null)
  // Removing a list element cannot be undone here, so it asks once more.
  const [confirmingRemoval, setConfirmingRemoval] = useState(false)

  const value = targets.find((target) => target.value_path === selectedPath) ?? null
  const identity = value ? entries[value.value_path] ?? horizontalGridOverrideIdentity(value) : {}
  const shownError = problem ?? error

  return (
    <Dialog
      aria-labelledby={titleId}
      fullWidth
      maxWidth={false}
      onClose={onClose}
      open={open && value !== null}
      PaperProps={{
        sx: (theme) => ({
          width: 'min(480px, calc(100vw - 32px))',
          m: 2,
          border: `1px solid ${theme.palette.mode === 'light' ? theme.palette.grey[400] : alpha(theme.palette.common.white, 0.28)}`,
          borderRadius: '8px',
          backgroundImage: 'none',
        }),
      }}
    >
      <Box sx={{ p: '20px 20px 0' }}>
        <Stack alignItems="flex-start" direction="row" justifyContent="space-between" spacing="12px">
          <Box>
            <Typography
              color="text.secondary"
              display="block"
              sx={{ fontSize: 9, fontWeight: 770, letterSpacing: '0.08em', mb: '3px', textTransform: 'uppercase' }}
            >
              Curator override
            </Typography>
            <Typography id={titleId} sx={{ fontSize: 15, fontWeight: 700, lineHeight: 1.25 }}>
              {`Set ${fieldLabel} by curator override`}
            </Typography>
          </Box>
          <IconButton aria-label="Close override editor" onClick={onClose} size="small" sx={{ height: 28, width: 28 }}>
            <CloseRoundedIcon sx={{ fontSize: 19 }} />
          </IconButton>
        </Stack>
      </Box>
      <DialogContent sx={{ p: '16px 20px 0 !important' }}>
        {value ? (
          <Stack spacing="12px">
            {targets.length > 1 ? (
              <TextField
                label="Value to override"
                onChange={(event) => {
                  setSelectedPath(event.target.value)
                  setProblem(null)
                  setConfirmingRemoval(false)
                  onSelectValue()
                }}
                select
                size="small"
                value={value.value_path}
              >
                {targets.map((target) => (
                  <MenuItem key={target.value_path} value={target.value_path}>
                    {targetLabel(fieldPath, fieldLabel, target)}
                  </MenuItem>
                ))}
              </TextField>
            ) : null}
            <Box>
              {value.mention ? (
                <Typography sx={{ fontSize: 12 }}>
                  <Box component="span" sx={{ fontWeight: 700 }}>Paper wording: </Box>
                  {value.mention}
                </Typography>
              ) : null}
              <Typography sx={{ fontSize: 12 }}>
                <Box component="span" sx={{ fontWeight: 700 }}>Lookup result: </Box>
                {value.lookup_result}
              </Typography>
              {value.validator_explanation ? (
                <Typography color="text.secondary" sx={{ fontSize: 12 }}>
                  <Box component="span" sx={{ fontWeight: 700 }}>Validator explanation: </Box>
                  {value.validator_explanation}
                </Typography>
              ) : null}
            </Box>
            {shownError ? (
              <Alert data-testid="horizontal-grid-override-error" severity="error">
                {shownError}
              </Alert>
            ) : null}
            {horizontalGridIdentityKeys(value).map((key) => (
              <TextField
                helperText={horizontalGridIdentityKeyRequired(value, key) ? undefined : 'Optional'}
                key={key}
                label={horizontalGridIdentityKeyLabel(value, key)}
                onChange={(event) => {
                  setEntries((current) => ({
                    ...current,
                    [value.value_path]: { ...identity, [key]: event.target.value },
                  }))
                  setProblem(null)
                }}
                required={horizontalGridIdentityKeyRequired(value, key)}
                size="small"
                value={identity[key] ?? ''}
              />
            ))}
            <Typography color="text.secondary" sx={{ fontSize: 10, lineHeight: 1.4 }}>
              Saving sets this value by curator override: it counts as validated, and a validator
              that disagrees later adds a warning instead of changing it.
              {value.curator_override ? ' Removing the override returns the value to unresolved.' : ''}
            </Typography>
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions sx={{ gap: '8px', p: '16px 20px 20px' }}>
        {value && horizontalGridRemovableElement(value) ? (
          <Button
            color="error"
            disabled={isSaving}
            onClick={() => {
              if (confirmingRemoval) {
                onRemoveElement(value)
                return
              }
              setConfirmingRemoval(true)
            }}
            variant="text"
          >
            {confirmingRemoval ? 'Confirm removal from the list' : 'Remove from the list'}
          </Button>
        ) : null}
        {value?.curator_override ? (
          <Button
            color="warning"
            disabled={isSaving}
            onClick={() => onRemove(value)}
            sx={{ mr: 'auto' }}
            variant="text"
          >
            Remove override
          </Button>
        ) : null}
        <Button disabled={isSaving} onClick={onClose} variant="outlined">
          Cancel
        </Button>
        <Button
          // Nothing to save until an identity key differs from its stored value.
          disabled={isSaving || !value || !horizontalGridOverrideChanged(value, identity)}
          onClick={() => {
            if (!value) {
              return
            }
            const incomplete = horizontalGridOverrideProblem(value, identity)
            if (incomplete) {
              setProblem(incomplete)
              return
            }
            onSave(value, identity)
          }}
          variant="contained"
        >
          Save override
        </Button>
      </DialogActions>
    </Dialog>
  )
}
