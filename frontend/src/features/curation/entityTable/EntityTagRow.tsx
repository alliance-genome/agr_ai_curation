import { Button, Chip, IconButton, TableCell, TableRow, Typography } from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditIcon from '@mui/icons-material/Edit'
import { alpha, useTheme } from '@mui/material/styles'
import type { EntityTag } from './types'
import {
  getEntityTypeLabel,
  type DbValidationStatus,
} from './types'

interface EntityTagRowProps {
  tag: EntityTag
  isSelected: boolean
  onSelect: (tagId: string) => void
  onAccept: (tagId: string) => void
  onReject: (tagId: string) => void
  onEdit: (tagId: string) => void
  onDelete: (tagId: string) => void
}

const DB_STATUS_COLOR: Record<DbValidationStatus, 'success' | 'warning' | 'error'> = {
  validated: 'success',
  ambiguous: 'warning',
  not_found: 'error',
}

const DB_STATUS_LABELS: Record<DbValidationStatus, string> = {
  validated: 'validated',
  ambiguous: 'ambiguous',
  not_found: 'not found',
}

export default function EntityTagRow({
  tag,
  isSelected,
  onSelect,
  onAccept,
  onReject,
  onEdit,
  onDelete,
}: EntityTagRowProps) {
  const theme = useTheme()

  const rowSx = {
    cursor: 'pointer',
    ...(isSelected && {
      borderLeft: `3px solid ${theme.palette.primary.main}`,
    }),
    ...(tag.decision === 'accepted' && {
      backgroundColor: alpha(theme.palette.success.main, 0.06),
    }),
    ...(tag.decision === 'rejected' && {
      opacity: 0.5,
    }),
  }

  const cellSx = { py: 0.75, px: 1, fontSize: '0.75rem' }

  const typeLabel = getEntityTypeLabel(tag.entity_type)

  return (
    <TableRow onClick={() => onSelect(tag.tag_id)} selected={isSelected} sx={rowSx}>
      <TableCell sx={{ ...cellSx, fontWeight: 600 }}>{tag.entity_name}</TableCell>
      <TableCell sx={cellSx}>{typeLabel}</TableCell>
      <TableCell sx={{ ...cellSx, fontStyle: 'italic' }}>{tag.species}</TableCell>
      <TableCell sx={cellSx}>{tag.topic}</TableCell>
      <TableCell sx={cellSx}>
        <Chip
          label={DB_STATUS_LABELS[tag.db_status]}
          size="small"
          color={DB_STATUS_COLOR[tag.db_status]}
          variant="outlined"
          sx={{ fontSize: '0.65rem', height: 20 }}
        />
      </TableCell>
      <TableCell sx={{ ...cellSx, color: 'text.secondary', fontSize: '0.65rem' }}>
        {tag.source === 'ai' ? 'AI' : 'Manual'}
      </TableCell>
      <TableCell sx={cellSx} onClick={(e) => e.stopPropagation()}>
        {tag.decision === 'pending' ? (
          <>
            <Button
              size="small"
              variant="outlined"
              color="success"
              onClick={() => onAccept(tag.tag_id)}
              sx={{ fontSize: '0.65rem', mr: 0.5, minWidth: 0, px: 1, py: 0.25 }}
            >
              Accept
            </Button>
            <Button
              size="small"
              variant="outlined"
              color="error"
              onClick={() => onReject(tag.tag_id)}
              sx={{ fontSize: '0.65rem', mr: 0.5, minWidth: 0, px: 1, py: 0.25 }}
            >
              Reject
            </Button>
          </>
        ) : (
          <Typography
            variant="caption"
            sx={{
              color: tag.decision === 'accepted' ? 'success.main' : 'text.secondary',
              fontWeight: 500,
              fontSize: '0.65rem',
            }}
          >
            {tag.decision === 'accepted' ? 'Accepted' : 'Rejected'}
          </Typography>
        )}
        <IconButton
          size="small"
          onClick={() => onEdit(tag.tag_id)}
          aria-label={`Edit ${tag.entity_name}`}
          sx={{ ml: 0.5, p: 0.25 }}
        >
          <EditIcon sx={{ fontSize: 14 }} />
        </IconButton>
        <IconButton
          size="small"
          color="error"
          onClick={() => onDelete(tag.tag_id)}
          aria-label={`Delete ${tag.entity_name}`}
          sx={{ ml: 0.25, p: 0.25 }}
        >
          <DeleteOutlineIcon sx={{ fontSize: 14 }} />
        </IconButton>
      </TableCell>
    </TableRow>
  )
}
