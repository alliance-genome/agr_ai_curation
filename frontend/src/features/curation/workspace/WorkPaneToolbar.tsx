import AddRoundedIcon from '@mui/icons-material/AddRounded'
import CheckRoundedIcon from '@mui/icons-material/CheckRounded'
import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import DoneAllRoundedIcon from '@mui/icons-material/DoneAllRounded'
import PictureInPictureAltRoundedIcon from '@mui/icons-material/PictureInPictureAltRounded'
import ViewStreamRoundedIcon from '@mui/icons-material/ViewStreamRounded'
import { Box, Button, Chip, Stack, Typography } from '@mui/material'
import { useTheme } from '@mui/material/styles'

import type { CurationCandidateStatus } from '@/features/curation/types'

export interface SelectedCandidateDecisionControl {
  label: string
  status: CurationCandidateStatus
  canAccept: boolean
  isBusy: boolean
  onAccept: () => void
  onReject: () => void
}

export interface WorkPaneToolbarProps {
  totalCount: number
  pendingCount: number
  validatedPendingCount: number
  validationCounts: {
    blocking: number
    openFindings: number
    stale: number
    validated: number
  }
  isPdfVisible: boolean
  selectedDecision: SelectedCandidateDecisionControl | null
  onAcceptAllValidated: () => void
  onAddObject: () => void
  onTogglePdf: () => void
}

export default function WorkPaneToolbar({
  totalCount,
  pendingCount,
  validatedPendingCount,
  validationCounts,
  isPdfVisible,
  selectedDecision,
  onAcceptAllValidated,
  onAddObject,
  onTogglePdf,
}: WorkPaneToolbarProps) {
  const theme = useTheme()

  return (
    <Box
      data-testid="work-pane-toolbar"
      data-theme-mode={theme.palette.mode}
      sx={{
        alignItems: 'center',
        backgroundColor: 'transparent',
        display: 'flex',
        flexWrap: 'wrap',
        gap: 1,
        justifyContent: 'space-between',
        minHeight: 0,
        py: 0.25,
        width: '100%',
      }}
    >
      <Stack spacing={0.5} sx={{
        minWidth: 0
      }}>
        <Stack
          direction="row"
          spacing={1}
          sx={{
            alignItems: "center",
            minWidth: 0
          }}>
          <Typography
            sx={{
              color: theme.palette.text.primary,
              fontWeight: 700,
            }}
            variant="subtitle2"
          >
            Review objects
          </Typography>
          <Chip
            label={`${totalCount} objects · ${pendingCount} pending`}
            size="small"
            sx={{
              borderRadius: 1,
              fontSize: '0.68rem',
              fontWeight: 700,
              height: 22,
            }}
            variant="outlined"
          />
        </Stack>
        <Stack
          aria-label="Authoritative validation summary"
          direction="row"
          spacing={1.25}
          useFlexGap
          sx={{
            flexWrap: "wrap"
          }}
        >
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            <Box aria-hidden component="span" sx={{
              color: "success.main"
            }}>●</Box> {validationCounts.validated} validated
          </Typography>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            <Box aria-hidden component="span" sx={{
              color: "warning.main"
            }}>●</Box> {validationCounts.blocking} need review
          </Typography>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            <Box aria-hidden component="span" sx={{
              color: "error.main"
            }}>●</Box> {validationCounts.stale} stale
          </Typography>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>{validationCounts.openFindings} open findings</Typography>
        </Stack>
      </Stack>
      <Stack
        direction="row"
        spacing={0.75}
        useFlexGap
        sx={{
          alignItems: "center",
          flexShrink: 0,
          flexWrap: "wrap"
        }}>
        {selectedDecision?.status === 'pending' ? (
          <Stack
            aria-label={`Decision for ${selectedDecision.label}`}
            direction="row"
            spacing={0.5}
          >
            <Button
              aria-label={`Accept ${selectedDecision.label}`}
              color="success"
              disabled={selectedDecision.isBusy || !selectedDecision.canAccept}
              onClick={selectedDecision.onAccept}
              size="small"
              startIcon={<CheckRoundedIcon fontSize="small" />}
              sx={{ borderRadius: 1, fontSize: '0.72rem', textTransform: 'none' }}
              variant="outlined"
            >
              Accept
            </Button>
            <Button
              aria-label={`Reject ${selectedDecision.label}`}
              color="error"
              disabled={selectedDecision.isBusy}
              onClick={selectedDecision.onReject}
              size="small"
              startIcon={<CloseRoundedIcon fontSize="small" />}
              sx={{ borderRadius: 1, fontSize: '0.72rem', textTransform: 'none' }}
              variant="text"
            >
              Reject
            </Button>
          </Stack>
        ) : selectedDecision ? (
          <Chip
            aria-label={`${selectedDecision.label} is ${selectedDecision.status}`}
            color={selectedDecision.status === 'accepted' ? 'success' : 'default'}
            label={selectedDecision.status}
            size="small"
            sx={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'capitalize' }}
            variant="outlined"
          />
        ) : null}
        <Button
          onClick={onTogglePdf}
          size="small"
          startIcon={isPdfVisible
            ? <ViewStreamRoundedIcon fontSize="small" />
            : <PictureInPictureAltRoundedIcon fontSize="small" />}
          sx={{ borderRadius: 1, fontSize: '0.72rem', textTransform: 'none' }}
          variant="outlined"
        >
          {isPdfVisible ? 'Focus grid' : 'Show PDF'}
        </Button>
        {/* Validation results remain visible, but execution controls are deliberately
            not mounted during this curator UI preview. */}
        <Button
          color="success"
          disabled={validatedPendingCount === 0}
          onClick={onAcceptAllValidated}
          size="small"
          startIcon={<DoneAllRoundedIcon fontSize="small" />}
          sx={{ borderRadius: 1, fontSize: '0.72rem', textTransform: 'none' }}
          variant="outlined"
        >
          Accept all validated
        </Button>
        <Button
          onClick={onAddObject}
          size="small"
          startIcon={<AddRoundedIcon fontSize="small" />}
          sx={{ borderRadius: 1, fontSize: '0.72rem', textTransform: 'none' }}
          variant="outlined"
        >
          Add object
        </Button>
      </Stack>
    </Box>
  );
}
