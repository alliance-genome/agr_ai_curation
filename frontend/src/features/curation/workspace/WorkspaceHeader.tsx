import type { ReactNode } from 'react'

import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded'
import ChevronLeftRoundedIcon from '@mui/icons-material/ChevronLeftRounded'
import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded'
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
  onPreviousSession?: () => void
  onNextSession?: () => void
  previousDisabled?: boolean
  nextDisabled?: boolean
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
  onPreviousSession,
  onNextSession,
  previousDisabled = true,
  nextDisabled = true,
}: WorkspaceHeaderProps) {
  const adapterChipColor = getAdapterChipColor(session.adapter)
  const statusChipColor = getStatusChipColor(session.status)

  return (
    <Box
      sx={(theme) => ({
        display: 'flex',
        flexDirection: 'column',
        gap: 1.5,
        px: { xs: 2, md: 2.5 },
        py: 2,
        borderRadius: theme.shape.borderRadius * 1.25,
        border: `1px solid ${alpha(theme.palette.divider, 0.85)}`,
        backgroundColor: alpha(theme.palette.background.paper, 0.92),
        boxShadow: `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.03)}`,
      })}
    >
      <Stack
        direction={{ xs: 'column', lg: 'row' }}
        spacing={2}
        justifyContent="space-between"
      >
        <Stack spacing={1}>
          <Button
            component={RouterLink}
            startIcon={<ArrowBackRoundedIcon />}
            sx={{ alignSelf: 'flex-start', px: 0 }}
            to={backHref}
          >
            Back to Inventory
          </Button>

          <Stack spacing={0.75}>
            <Typography variant="h4">
              {session.document.title}
            </Typography>
            <Typography color="text.secondary" variant="body2">
              {getDocumentMetaLabel(session)}
            </Typography>
          </Stack>
        </Stack>

        <Stack
          alignItems={{ xs: 'flex-start', lg: 'flex-end' }}
          justifyContent="space-between"
          spacing={1.5}
        >
          <Stack direction="row" flexWrap="wrap" spacing={1} useFlexGap>
            <Chip
              color={adapterChipColor}
              label={getAdapterLabel(session.adapter)}
              size="small"
              variant="outlined"
            />
            <Chip
              color="success"
              label={`${session.progress.reviewed_candidates}/${session.progress.total_candidates} reviewed`}
              size="small"
              variant="outlined"
            />
            <Chip
              color={statusChipColor}
              label={getStatusLabel(session.status)}
              size="small"
              variant={statusChipColor === 'default' ? 'outlined' : 'filled'}
            />
          </Stack>

          {navigationSlot ? (
            <Box data-testid="workspace-header-navigation-slot">
              {navigationSlot}
            </Box>
          ) : (
            <Stack direction="row" spacing={1}>
              <Button
                disabled={previousDisabled}
                onClick={onPreviousSession}
                size="small"
                startIcon={<ChevronLeftRoundedIcon />}
                variant="outlined"
              >
                Prev Session
              </Button>
              <Button
                disabled={nextDisabled}
                onClick={onNextSession}
                size="small"
                endIcon={<ChevronRightRoundedIcon />}
                variant="outlined"
              >
                Next Session
              </Button>
            </Stack>
          )}
        </Stack>
      </Stack>
    </Box>
  )
}
