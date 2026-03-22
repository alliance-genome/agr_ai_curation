import {
  Box,
  Button,
  Paper,
  Skeleton,
  Stack,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { CurationSessionStats } from '../types'

interface InventoryStatsCardsProps {
  stats?: CurationSessionStats
  isPending: boolean
  errorMessage?: string
  onRetry?: () => void
}

interface StatCardDefinition {
  id: string
  label: string
  value: (stats: CurationSessionStats) => number
  subtitle: (stats: CurationSessionStats) => string
  accent: string
}

const CARD_DEFINITIONS: StatCardDefinition[] = [
  {
    id: 'total',
    label: 'Total Sessions',
    value: (stats) => stats.total_sessions,
    subtitle: (stats) => `across ${formatNumber(stats.domain_count)} ${pluralize(stats.domain_count, 'domain')}`,
    accent: '#f8fafc',
  },
  {
    id: 'new',
    label: 'New / Unreviewed',
    value: (stats) => stats.new_sessions,
    subtitle: () => 'ready for curator review',
    accent: '#a855f7',
  },
  {
    id: 'in-progress',
    label: 'In Progress',
    value: (stats) => stats.in_progress_sessions,
    subtitle: (stats) =>
      `${formatNumber(stats.assigned_to_current_user)} by you, ${formatNumber(stats.assigned_to_others)} by others`,
    accent: '#f59e0b',
  },
  {
    id: 'submitted',
    label: 'Submitted',
    value: (stats) => stats.submitted_sessions,
    subtitle: (stats) => `last 7 days: ${formatNumber(stats.submitted_last_7_days)}`,
    accent: '#22c55e',
  },
  {
    id: 'rejected',
    label: 'Rejected',
    value: (stats) => stats.rejected_sessions,
    subtitle: () => 'low quality extractions',
    accent: '#ef4444',
  },
]

function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-US').format(value)
}

function pluralize(value: number, singular: string, plural = `${singular}s`): string {
  return value === 1 ? singular : plural
}

function InventoryStatsCard({
  accent,
  label,
  subtitle,
  value,
}: {
  accent: string
  label: string
  subtitle: string
  value: number
}) {
  const theme = useTheme()

  return (
    <Paper
      variant="outlined"
      sx={{
        position: 'relative',
        overflow: 'hidden',
        minHeight: 168,
        p: 2.5,
        borderColor: alpha(accent, accent === '#f8fafc' ? 0.14 : 0.34),
        backgroundColor: alpha(accent, accent === '#f8fafc' ? 0.04 : 0.12),
        backgroundImage: `linear-gradient(135deg, ${alpha(accent, accent === '#f8fafc' ? 0.14 : 0.24)} 0%, ${alpha(accent, 0.04)} 100%)`,
        boxShadow: `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.05)}`,
        '&::after': {
          content: '""',
          position: 'absolute',
          right: -24,
          bottom: -28,
          width: 104,
          height: 104,
          borderRadius: '50%',
          backgroundColor: alpha(accent, 0.14),
          filter: 'blur(6px)',
        },
      }}
    >
      <Stack spacing={1.2} sx={{ position: 'relative', zIndex: 1 }}>
        <Box
          sx={{
            width: 36,
            height: 4,
            borderRadius: 999,
            backgroundColor: accent,
          }}
        />
        <Typography
          variant="overline"
          sx={{
            color: accent === '#f8fafc' ? alpha(theme.palette.common.white, 0.72) : accent,
            fontSize: '0.72rem',
            fontWeight: 700,
            letterSpacing: '0.08em',
            lineHeight: 1.2,
          }}
        >
          {label}
        </Typography>
        <Typography
          variant="h3"
          sx={{
            color: theme.palette.common.white,
            fontSize: { xs: '2rem', md: '2.25rem' },
            fontWeight: 700,
            lineHeight: 1,
          }}
        >
          {formatNumber(value)}
        </Typography>
        <Typography color="text.secondary" variant="body2">
          {subtitle}
        </Typography>
      </Stack>
    </Paper>
  )
}

function LoadingSkeletonCards() {
  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: {
          xs: '1fr',
          sm: 'repeat(2, minmax(0, 1fr))',
          lg: 'repeat(5, minmax(0, 1fr))',
        },
      }}
    >
      {CARD_DEFINITIONS.map((card) => (
        <Paper
          key={card.id}
          data-testid="inventory-stats-card-skeleton"
          variant="outlined"
          sx={{
            minHeight: 168,
            p: 2.5,
          }}
        >
          <Stack spacing={1.2}>
            <Skeleton variant="rounded" width={36} height={4} />
            <Skeleton variant="text" width="42%" height={20} />
            <Skeleton variant="text" width="58%" height={52} />
            <Skeleton variant="text" width="76%" height={18} />
          </Stack>
        </Paper>
      ))}
    </Box>
  )
}

export default function InventoryStatsCards({
  stats,
  isPending,
  errorMessage,
  onRetry,
}: InventoryStatsCardsProps) {
  if (isPending) {
    return <LoadingSkeletonCards />
  }

  if (errorMessage || !stats) {
    return (
      <Paper
        variant="outlined"
        sx={{
          p: 2.5,
        }}
      >
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={2}
          alignItems={{ xs: 'flex-start', sm: 'center' }}
          justifyContent="space-between"
        >
          <Stack spacing={0.5}>
            <Typography variant="h6">Inventory stats unavailable</Typography>
            <Typography color="text.secondary" variant="body2">
              {errorMessage
                ? 'Status counts are temporarily unavailable. Session filters still work normally.'
                : 'Inventory counts will appear here once the stats service responds.'}
            </Typography>
          </Stack>
          {onRetry && (
            <Button onClick={onRetry} size="small" variant="outlined">
              Try again
            </Button>
          )}
        </Stack>
      </Paper>
    )
  }

  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: {
          xs: '1fr',
          sm: 'repeat(2, minmax(0, 1fr))',
          lg: 'repeat(5, minmax(0, 1fr))',
        },
      }}
    >
      {CARD_DEFINITIONS.map((card) => (
        <InventoryStatsCard
          key={card.id}
          accent={card.accent}
          label={card.label}
          subtitle={card.subtitle(stats)}
          value={card.value(stats)}
        />
      ))}
    </Box>
  )
}
