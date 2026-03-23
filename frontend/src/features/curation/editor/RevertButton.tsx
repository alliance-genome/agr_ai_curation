import UndoRoundedIcon from '@mui/icons-material/UndoRounded'
import { Button } from '@mui/material'

export interface RevertButtonProps {
  canRevert: boolean
  onRevert: () => void
}

export default function RevertButton({
  canRevert,
  onRevert,
}: RevertButtonProps) {
  if (!canRevert) {
    return null
  }

  return (
    <Button
      onClick={onRevert}
      size="small"
      startIcon={<UndoRoundedIcon fontSize="inherit" />}
      type="button"
      variant="text"
    >
      Revert to AI
    </Button>
  )
}
