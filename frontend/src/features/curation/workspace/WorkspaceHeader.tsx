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
      sx={(theme) => ({
        display: 'flex',
        flexDirection: { xs: 'column', xl: 'row' },
        alignItems: { xs: 'stretch', xl: 'center' },
        gap: { xs: 1, xl: 1.5 },
        px: { xs: 1.25, md: 1.5 },
        py: 1,
        borderRadius: theme.shape.borderRadius,
        border: `1px solid ${alpha(theme.palette.primary.light, 0.18)}`,
        background:
          `linear-gradient(180deg, ${alpha(theme.palette.common.white, 0.035)}, ${alpha(theme.palette.common.white, 0.01)}), #071524`,
        boxShadow: `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.05)}, 0 18px 42px ${alpha(theme.palette.common.black, 0.22)}`,
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
            color: theme.palette.primary.light,
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

        <Box
          sx={(theme) => ({
            alignItems: 'center',
            borderLeft: `1px solid ${alpha(theme.palette.common.white, 0.1)}`,
            display: { xs: 'none', sm: 'flex' },
            flexShrink: 0,
            height: 28,
            pl: 1.25,
          })}
        >
          <DescriptionOutlinedIcon sx={{ color: 'text.secondary', fontSize: 22 }} />
        </Box>

        <Typography
          variant="subtitle2"
          sx={{
            flexShrink: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontWeight: 600,
            letterSpacing: -0.1,
          }}
          title={getDocumentMetaLabel(session)}
        >
          {session.document.title}
        </Typography>

        <Typography
          color="text.secondary"
          variant="caption"
          sx={{
            flexShrink: 0,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            display: { xs: 'none', lg: 'block' },
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            maxWidth: 210,
          }}
          title={session.session_id}
        >
          {compactSessionId(session.session_id)}
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
