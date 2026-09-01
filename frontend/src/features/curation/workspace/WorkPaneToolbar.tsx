import AddRoundedIcon from '@mui/icons-material/AddRounded'
import DoneAllRoundedIcon from '@mui/icons-material/DoneAllRounded'
import PictureInPictureAltRoundedIcon from '@mui/icons-material/PictureInPictureAltRounded'
import ViewStreamRoundedIcon from '@mui/icons-material/ViewStreamRounded'
import { Box, Button, Chip, Stack, Typography } from '@mui/material'
import { useTheme } from '@mui/material/styles'

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
      <Stack spacing={0.5} minWidth={0}>
        <Stack direction="row" spacing={1} alignItems="center" minWidth={0}>
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
          flexWrap="wrap"
          spacing={1.25}
          useFlexGap
        >
          <Typography color="text.secondary" variant="caption">
            <Box aria-hidden color="success.main" component="span">●</Box> {validationCounts.validated} validated
          </Typography>
          <Typography color="text.secondary" variant="caption">
            <Box aria-hidden color="warning.main" component="span">●</Box> {validationCounts.blocking} need review
          </Typography>
          <Typography color="text.secondary" variant="caption">
            <Box aria-hidden color="error.main" component="span">●</Box> {validationCounts.stale} stale
          </Typography>
          <Typography color="text.secondary" variant="caption">{validationCounts.openFindings} open findings</Typography>
        </Stack>
      </Stack>
      <Stack direction="row" spacing={0.75} alignItems="center" flexShrink={0} flexWrap="wrap" useFlexGap>
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
  )
}
