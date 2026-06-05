import AddRoundedIcon from '@mui/icons-material/AddRounded'
import DoneAllRoundedIcon from '@mui/icons-material/DoneAllRounded'
import { Box, Button, Chip, Stack, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

export interface WorkPaneToolbarProps {
  totalCount: number
  pendingCount: number
  validatedPendingCount: number
  onAcceptAllValidated: () => void
  onAddObject: () => void
}

export default function WorkPaneToolbar({
  totalCount,
  pendingCount,
  validatedPendingCount,
  onAcceptAllValidated,
  onAddObject,
}: WorkPaneToolbarProps) {
  const theme = useTheme()

  return (
    <Box
      data-testid="work-pane-toolbar"
      sx={{
        alignItems: 'center',
        borderBottom: `1px solid ${alpha(theme.palette.common.white, 0.08)}`,
        display: 'flex',
        gap: 1,
        justifyContent: 'space-between',
        minHeight: 48,
        px: 1.25,
        py: 0.75,
      }}
    >
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
      <Stack direction="row" spacing={0.75} alignItems="center" flexShrink={0}>
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
