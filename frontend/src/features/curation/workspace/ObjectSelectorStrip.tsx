import { useMemo, useState } from 'react'

import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import KeyboardArrowLeftRoundedIcon from '@mui/icons-material/KeyboardArrowLeftRounded'
import KeyboardArrowRightRoundedIcon from '@mui/icons-material/KeyboardArrowRightRounded'
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded'
import {
  Box,
  Button,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { WorkspaceEnvelopeObjectReviewRow } from './envelopeObjectReviewRows'
import {
  adjacentCandidateId,
  objectSelectorLabel,
  objectSelectorType,
  progressSegments,
  selectorPosition,
  type ObjectSelectorProgressKind,
} from './objectSelector'
import DeleteObjectDialog from './DeleteObjectDialog'

export interface ObjectSelectorStripProps {
  activeCandidateId: string | null
  onDelete?: (candidateId: string) => void
  onSelect: (candidateId: string) => void
  rows: WorkspaceEnvelopeObjectReviewRow[]
}

const SEGMENT_COLOR: Record<ObjectSelectorProgressKind, string> = {
  current: 'primary.main',
  done: 'success.main',
  pending: 'divider',
  rejected: 'text.disabled',
}

export default function ObjectSelectorStrip({
  activeCandidateId,
  onDelete,
  onSelect,
  rows,
}: ObjectSelectorStripProps) {
  const theme = useTheme()
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<WorkspaceEnvelopeObjectReviewRow | null>(null)
  const candidates = useMemo(() => rows.map((row) => row.candidate), [rows])
  const activeRow = rows.find((row) => row.candidate.candidate_id === activeCandidateId) ?? null
  const position = selectorPosition(candidates, activeCandidateId)
  const previousId = adjacentCandidateId(rows, activeCandidateId, 'previous')
  const nextId = adjacentCandidateId(rows, activeCandidateId, 'next')
  const segments = progressSegments(candidates, activeCandidateId)
  const menuOpen = Boolean(anchorEl)

  if (rows.length === 0) {
    return null
  }

  return (
    <Box
      data-testid="object-selector-strip"
      sx={{
        borderBottom: `1px solid ${alpha(theme.palette.common.white, 0.08)}`,
        px: 1.25,
        py: 1,
      }}
    >
      <Stack direction="row" spacing={0.75} alignItems="center" minWidth={0}>
        <Tooltip title="Previous object">
          <span>
            <IconButton
              aria-label="Previous object"
              disabled={!previousId}
              onClick={() => {
                if (previousId) {
                  onSelect(previousId)
                }
              }}
              size="small"
            >
              <KeyboardArrowLeftRoundedIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>

        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} alignItems="baseline" minWidth={0}>
            <Typography
              sx={{
                color: alpha(theme.palette.common.white, 0.94),
                fontWeight: 700,
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              variant="body2"
            >
              {activeRow ? objectSelectorLabel(activeRow) : 'Select an object'}
            </Typography>
            {activeRow ? (
              <Typography
                color="text.secondary"
                sx={{ flexShrink: 0, fontSize: '0.72rem', fontWeight: 600 }}
                variant="caption"
              >
                {objectSelectorType(activeRow)}
              </Typography>
            ) : null}
            <Typography
              color="text.secondary"
              sx={{ flexShrink: 0, fontSize: '0.72rem', fontWeight: 700 }}
              variant="caption"
            >
              {position.position || '-'} of {position.total}
            </Typography>
          </Stack>
        </Box>

        <Button
          aria-controls={menuOpen ? 'object-selector-menu' : undefined}
          aria-expanded={menuOpen ? 'true' : undefined}
          aria-haspopup="listbox"
          endIcon={<ExpandMoreRoundedIcon fontSize="small" />}
          onClick={(event) => setAnchorEl(event.currentTarget)}
          size="small"
          sx={{ borderRadius: 1, flexShrink: 0, textTransform: 'none' }}
          variant="outlined"
        >
          All objects
        </Button>
        <Menu
          anchorEl={anchorEl}
          id="object-selector-menu"
          MenuListProps={{ role: 'listbox' }}
          onClose={() => setAnchorEl(null)}
          open={menuOpen}
        >
          {rows.map((row) => {
            const candidateId = row.candidate.candidate_id
            const selected = candidateId === activeCandidateId
            const label = objectSelectorLabel(row)

            return (
              <MenuItem
                aria-selected={selected}
                key={candidateId}
                onClick={() => {
                  setAnchorEl(null)
                  onSelect(candidateId)
                }}
                role="option"
                selected={selected}
                sx={{ gap: 1, justifyContent: 'space-between' }}
              >
                <Stack spacing={0.1} minWidth={0}>
                  <Typography sx={{ fontWeight: 700 }} variant="body2">
                    {label}
                  </Typography>
                  <Typography color="text.secondary" variant="caption">
                    {objectSelectorType(row)}
                  </Typography>
                </Stack>
                {onDelete ? (
                  <IconButton
                    aria-label={`Delete object ${label}`}
                    color="error"
                    onClick={(event) => {
                      event.stopPropagation()
                      setAnchorEl(null)
                      setDeleteTarget(row)
                    }}
                    size="small"
                  >
                    <DeleteOutlineRoundedIcon fontSize="small" />
                  </IconButton>
                ) : null}
              </MenuItem>
            )
          })}
        </Menu>

        <Tooltip title="Next object">
          <span>
            <IconButton
              aria-label="Next object"
              disabled={!nextId}
              onClick={() => {
                if (nextId) {
                  onSelect(nextId)
                }
              }}
              size="small"
            >
              <KeyboardArrowRightRoundedIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      <Box
        aria-hidden="true"
        sx={{
          display: 'grid',
          gap: 0.35,
          gridTemplateColumns: `repeat(${Math.max(segments.length, 1)}, minmax(0, 1fr))`,
          mt: 0.75,
        }}
      >
        {segments.map((segment) => (
          <Box
            key={segment.id}
            sx={{
              backgroundColor: SEGMENT_COLOR[segment.kind],
              borderRadius: 999,
              height: 4,
              opacity: segment.kind === 'pending' ? 0.55 : 1,
            }}
          />
        ))}
      </Box>
      <DeleteObjectDialog
        candidateLabel={deleteTarget ? objectSelectorLabel(deleteTarget) : 'this object'}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) {
            onDelete?.(deleteTarget.candidate.candidate_id)
          }
          setDeleteTarget(null)
        }}
        open={deleteTarget !== null}
      />
    </Box>
  )
}
