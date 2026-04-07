import type { ReactNode } from 'react'

import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded'
import {
  Box,
  Button,
  Chip,
  Stack,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import { Link as RouterLink } from 'react-router-dom'

import {
  getAdapterChipColor,
  getAdapterLabel,
  getStatusChipColor,
  getStatusLabel,
} from '@/features/curation/inventory/inventoryPresentation'
import type { CurationReviewSession } from '@/features/curation/types'

export interface WorkspaceHeaderProps {
  session: CurationReviewSession
  backHref?: string
  navigationSlot?: ReactNode
}

function getDocumentMetaLabel(session: CurationReviewSession): string {
  const parts: string[] = []

  if (session.document.pmid) {
    parts.push(`PMID ${session.document.pmid}`)
  }
  if (session.document.doi) {
    parts.push(`DOI ${session.document.doi}`)
  }

  if (parts.length > 0) {
    return parts.join(' • ')
  }

  return session.document.citation_label ?? session.document.document_id
}

export default function WorkspaceHeader({
  session,
  backHref = '/curation',
  navigationSlot,
}: WorkspaceHeaderProps) {
  const adapterChipColor = getAdapterChipColor(session.adapter)
  const statusChipColor = getStatusChipColor(session.status)

  return (
    <Box
      sx={(theme) => ({
        display: 'flex',
        flexDirection: 'column',
        gap: 1,
        px: { xs: 1.5, md: 2 },
        py: 1.25,
        borderRadius: theme.shape.borderRadius * 1.25,
        border: `1px solid ${alpha(theme.palette.divider, 0.85)}`,
        backgroundColor: alpha(theme.palette.background.paper, 0.92),
        boxShadow: `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.03)}`,
      })}
    >
      {/* Row 1: Back + Title + Meta label + Chips */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ minWidth: 0 }}
      >
        <Button
          aria-label="Back to inventory"
          component={RouterLink}
          size="small"
          startIcon={<ArrowBackRoundedIcon sx={{ fontSize: '1rem' }} />}
          sx={{ px: 0.5, minWidth: 'auto', flexShrink: 0, fontSize: '0.75rem' }}
          to={backHref}
        >
          Back
        </Button>

        <Typography
          variant="subtitle2"
          sx={{
            flexShrink: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontWeight: 600,
          }}
          title={getDocumentMetaLabel(session)}
        >
          {session.document.title}
        </Typography>

        <Typography
          color="text.secondary"
          variant="caption"
          sx={{
            flexShrink: 2,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            display: { xs: 'none', lg: 'block' },
          }}
        >
          {getDocumentMetaLabel(session)}
        </Typography>

        <Box sx={{ flexGrow: 1 }} />

        <Stack direction="row" flexShrink={0} spacing={0.75} useFlexGap>
          <Chip
            color={adapterChipColor}
            label={getAdapterLabel(session.adapter)}
            size="small"
            variant="outlined"
            sx={{ height: 22, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
          />
          <Chip
            color="success"
            label={`${session.progress.reviewed_candidates}/${session.progress.total_candidates}`}
            size="small"
            variant="outlined"
            sx={{ height: 22, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
          />
          <Chip
            color={statusChipColor}
            label={getStatusLabel(session.status)}
            size="small"
            variant={statusChipColor === 'default' ? 'outlined' : 'filled'}
            sx={{ height: 22, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
          />
        </Stack>
      </Stack>

      {/* Row 2: Navigation slot (right-aligned) */}
      {navigationSlot ? (
        <Stack
          direction="row"
          justifyContent="flex-end"
          data-testid="workspace-header-navigation-slot"
        >
          {navigationSlot}
        </Stack>
      ) : null}
    </Box>
  )
}
