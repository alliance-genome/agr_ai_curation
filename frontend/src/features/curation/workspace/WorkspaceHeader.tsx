import type { ReactNode } from 'react'

import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
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

function compactSessionId(sessionId: string): string {
  if (sessionId.length <= 18) {
    return sessionId
  }

  return `${sessionId.slice(0, 8)}-${sessionId.slice(-8)}`
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
      data-testid="workspace-header"
      sx={(theme) => ({
        display: 'flex',
        flexDirection: { xs: 'column', xl: 'row' },
        alignItems: { xs: 'stretch', xl: 'center' },
        gap: { xs: 1, xl: 1.5 },
        px: { xs: 1.5, md: 2 },
        py: { xs: 1.25, md: 1.5 },
        borderRadius: `${theme.shape.borderRadius}px ${theme.shape.borderRadius}px 0 0`,
        border: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
        boxShadow: `0 1px 2px ${alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.26 : 0.08)}`,
      })}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ flex: '1 1 auto', minWidth: 0 }}
      >
        <Button
          aria-label="Back to inventory"
          component={RouterLink}
          size="small"
          startIcon={<ArrowBackRoundedIcon sx={{ fontSize: '1rem' }} />}
          sx={(theme) => ({
            color: theme.palette.mode === 'dark'
              ? theme.palette.primary.light
              : theme.palette.primary.main,
            flexShrink: 0,
            fontSize: '0.78rem',
            fontWeight: 500,
            letterSpacing: 0,
            minWidth: 'auto',
            px: 0.5,
            textTransform: 'none',
            '&:hover': {
              backgroundColor: alpha(theme.palette.primary.main, 0.12),
            },
          })}
          to={backHref}
        >
          Back
        </Button>

        <DescriptionOutlinedIcon
          sx={{ color: 'text.secondary', display: { xs: 'none', sm: 'block' }, fontSize: 24 }}
        />

        <Stack minWidth={0} spacing={0.25}>
          <Typography
            color="text.secondary"
            sx={{ fontSize: '0.66rem', fontWeight: 750, letterSpacing: '0.09em', textTransform: 'uppercase' }}
          >
            Curation workspace / {getAdapterLabel(session.adapter)}
          </Typography>
          <Stack alignItems="center" direction="row" spacing={1}>
            <Typography
              component="h1"
              sx={{ fontSize: { xs: '1rem', md: '1.25rem' }, fontWeight: 700, letterSpacing: '-0.02em' }}
              variant="h6"
            >
              {getAdapterLabel(session.adapter)} review
            </Typography>
            <Chip
              label={`${session.progress.total_candidates} ${session.progress.total_candidates === 1 ? 'record' : 'records'}`}
              size="small"
              sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { px: 0.8, fontSize: '0.66rem', fontWeight: 700 } }}
            />
          </Stack>
          <Typography color="text.secondary" noWrap title={session.document.title} variant="caption">
            {session.document.title} · {getDocumentMetaLabel(session)}
          </Typography>
        </Stack>

        <Box sx={{ flexGrow: 1 }} />

        <Stack direction="row" flexShrink={0} spacing={0.75} useFlexGap>
          <Chip
            color={adapterChipColor}
            label={getAdapterLabel(session.adapter)}
            size="small"
            variant="outlined"
            sx={{ borderRadius: 1, height: 26, '& .MuiChip-label': { px: 0.9, fontSize: '0.72rem', fontWeight: 600 } }}
          />
          <Chip
            color="success"
            label={`${session.progress.reviewed_candidates}/${session.progress.total_candidates}`}
            size="small"
            variant="outlined"
            sx={{ borderRadius: 1, height: 26, '& .MuiChip-label': { px: 0.9, fontSize: '0.72rem', fontWeight: 600 } }}
          />
          <Chip
            color={statusChipColor}
            label={getStatusLabel(session.status)}
            size="small"
            variant={statusChipColor === 'default' ? 'outlined' : 'filled'}
            sx={{ borderRadius: 1, height: 26, '& .MuiChip-label': { px: 0.9, fontSize: '0.72rem', fontWeight: 600 } }}
          />
        </Stack>
        <Typography
          color="text.secondary"
          sx={{ display: { xs: 'none', xl: 'block' }, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}
          title={session.session_id}
          variant="caption"
        >
          {compactSessionId(session.session_id)}
        </Typography>
      </Stack>

      {navigationSlot ? (
        <Stack
          direction="row"
          justifyContent={{ xs: 'flex-start', xl: 'flex-end' }}
          data-testid="workspace-header-navigation-slot"
          sx={{ flex: '0 0 auto' }}
        >
          {navigationSlot}
        </Stack>
      ) : null}
    </Box>
  )
}
