import { Box, Button, Chip, Typography } from '@mui/material'

interface EntityTagToolbarProps {
  totalCount: number
  pendingCount: number
  onAcceptAllValidated: () => void
  onAddEntity: () => void
}

export default function EntityTagToolbar({
  totalCount,
  pendingCount,
  onAcceptAllValidated,
  onAddEntity,
}: EntityTagToolbarProps) {
  return (
    <Box
      sx={{
        px: 1.5,
        py: 0.75,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: 1,
        borderColor: 'divider',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
          Entity Tags
        </Typography>
        <Chip
          label={`${totalCount} entities \u00b7 ${pendingCount} pending`}
          size="small"
          color="primary"
          variant="outlined"
          sx={{ fontSize: '0.65rem', height: 22 }}
        />
      </Box>
      <Box sx={{ display: 'flex', gap: 0.75 }}>
        <Button
          size="small"
          variant="outlined"
          color="success"
          disabled={pendingCount === 0}
          onClick={onAcceptAllValidated}
          sx={{ fontSize: '0.65rem', textTransform: 'none' }}
        >
          Accept All Validated
        </Button>
        <Button
          size="small"
          variant="outlined"
          onClick={onAddEntity}
          sx={{ fontSize: '0.65rem', textTransform: 'none' }}
        >
          + Add Entity
        </Button>
      </Box>
    </Box>
  )
}
