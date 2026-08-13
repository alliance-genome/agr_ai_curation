import AddRoundedIcon from '@mui/icons-material/AddRounded'
import DoneAllRoundedIcon from '@mui/icons-material/DoneAllRounded'
import PictureInPictureAltRoundedIcon from '@mui/icons-material/PictureInPictureAltRounded'
import RuleRoundedIcon from '@mui/icons-material/RuleRounded'
import ViewStreamRoundedIcon from '@mui/icons-material/ViewStreamRounded'
import { Box, Button, Chip, CircularProgress, Stack, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

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
  isValidatingAll: boolean
  onAcceptAllValidated: () => void
  onAddObject: () => void
  onTogglePdf: () => void
  onValidateAll: () => void
}

export default function WorkPaneToolbar({
  totalCount,
  pendingCount,
  validatedPendingCount,
  validationCounts,
  isPdfVisible,
  isValidatingAll,
  onAcceptAllValidated,
  onAddObject,
  onTogglePdf,
  onValidateAll,
}: WorkPaneToolbarProps) {
  const theme = useTheme()

  return (
    <Box
      data-testid="work-pane-toolbar"
      sx={{
        alignItems: 'center',
        borderBottom: `1px solid ${alpha(theme.palette.common.white, 0.08)}`,
        display: 'flex',
        flexWrap: 'wrap',
        gap: 1,
        justifyContent: 'space-between',
        minHeight: 48,
        px: 1.25,
        py: 0.75,
      }}
    >
      <Stack spacing={0.5} minWidth={0}>
        <Stack direction="row" spacing={1} alignItems="center" minWidth={0}>
          <Typography
            sx={{
              color: alpha(theme.palette.common.white, 0.94),
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
        <Typography
          aria-label="Authoritative validation summary"
          color="text.secondary"
          variant="caption"
        >
          {`${validationCounts.validated} validated · ${validationCounts.blocking} blocking · ${validationCounts.stale} stale · ${validationCounts.openFindings} open findings`}
        </Typography>
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
        <Button
          disabled={isValidatingAll || pendingCount === 0}
          onClick={onValidateAll}
          size="small"
          startIcon={isValidatingAll
            ? <CircularProgress color="inherit" size={16} />
            : <RuleRoundedIcon fontSize="small" />}
          sx={{ borderRadius: 1, fontSize: '0.72rem', textTransform: 'none' }}
          variant="outlined"
        >
          {isValidatingAll ? 'Validating all…' : 'Validate all'}
        </Button>
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
