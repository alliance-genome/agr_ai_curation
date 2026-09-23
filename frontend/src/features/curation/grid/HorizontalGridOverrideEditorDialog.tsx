import { useEffect, useId, useState } from 'react'

import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  IconButton,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import type { DomainEnvelopeReviewResolvedValue } from '@/features/curation/types'
import {
  horizontalGridOverrideIdentity,
  horizontalGridOverrideProblem,
  type HorizontalGridOverrideIdentity,
} from './horizontalGridOverride'

export interface HorizontalGridOverrideEditorDialogProps {
  // A rejected save (the backend's own words), shown inline; the editor stays open.
  error: string | null
  fieldLabel: string
  isSaving: boolean
  onClose: () => void
  onRemove: () => void
  onSave: (identity: HorizontalGridOverrideIdentity) => void
  value: DomainEnvelopeReviewResolvedValue | null
}

/**
 * A curator validation override: the curator sets a value's identifier and
 * name together, in one save. The paper wording, the validator's result and
 * its words stay as they are, for reference.
 */
export default function HorizontalGridOverrideEditorDialog({
  error,
  fieldLabel,
  isSaving,
  onClose,
  onRemove,
  onSave,
  value,
}: HorizontalGridOverrideEditorDialogProps) {
  const titleId = useId()
  const [identity, setIdentity] = useState<HorizontalGridOverrideIdentity>({ identifier: '', name: '' })
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    if (value) {
      setIdentity(horizontalGridOverrideIdentity(value))
      setProblem(null)
    }
  }, [value])

  const shownError = problem ?? error

  return (
    <Dialog
      aria-labelledby={titleId}
      fullWidth
      maxWidth={false}
      onClose={onClose}
      open={value !== null}
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
            {value.id_key ? (
              <TextField
                label="Identifier"
                onChange={(event) => {
                  setIdentity((current) => ({ ...current, identifier: event.target.value }))
                  setProblem(null)
                }}
                size="small"
                value={identity.identifier}
              />
            ) : null}
            {value.label_key ? (
              <TextField
                label="Name"
                onChange={(event) => {
                  setIdentity((current) => ({ ...current, name: event.target.value }))
                  setProblem(null)
                }}
                size="small"
                value={identity.name}
              />
            ) : null}
            <Typography color="text.secondary" sx={{ fontSize: 10, lineHeight: 1.4 }}>
              Saving sets this value by curator override: it counts as validated, and a validator
              that disagrees later adds a warning instead of changing it.
              {value.curator_override ? ' Removing the override returns the value to unresolved.' : ''}
            </Typography>
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions sx={{ gap: '8px', p: '16px 20px 20px' }}>
        {value?.curator_override ? (
          <Button
            color="warning"
            disabled={isSaving}
            onClick={onRemove}
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
          disabled={isSaving || !value}
          onClick={() => {
            if (!value) {
              return
            }
            const incomplete = horizontalGridOverrideProblem(value, identity)
            if (incomplete) {
              setProblem(incomplete)
              return
            }
            onSave(identity)
          }}
          variant="contained"
        >
          Save override
        </Button>
      </DialogActions>
    </Dialog>
  )
}
