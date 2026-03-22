import type { ComponentProps } from 'react'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { CurationSessionStats } from '../types'
import InventoryStatsCards from './InventoryStatsCards'

const theme = createTheme()

const stats: CurationSessionStats = {
  total_sessions: 124,
  domain_count: 7,
  new_sessions: 28,
  in_progress_sessions: 41,
  ready_for_submission_sessions: 18,
  paused_sessions: 6,
  submitted_sessions: 33,
  rejected_sessions: 5,
  assigned_to_current_user: 9,
  assigned_to_others: 14,
  submitted_last_7_days: 11,
}

function renderCards(props: Partial<ComponentProps<typeof InventoryStatsCards>> = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <InventoryStatsCards
        errorMessage={undefined}
        isLoading={false}
        stats={stats}
        {...props}
      />
    </ThemeProvider>
  )
}

describe('InventoryStatsCards', () => {
  it('renders the five inventory summary cards from stats data', () => {
    renderCards()

    expect(screen.getByText('Total Sessions')).toBeInTheDocument()
    expect(screen.getByText('124')).toBeInTheDocument()
    expect(screen.getByText('across 7 domains')).toBeInTheDocument()

    expect(screen.getByText('New / Unreviewed')).toBeInTheDocument()
    expect(screen.getByText('28')).toBeInTheDocument()
    expect(screen.getByText('ready for curator review')).toBeInTheDocument()

    expect(screen.getByText('In Progress')).toBeInTheDocument()
    expect(screen.getByText('41')).toBeInTheDocument()
    expect(screen.getByText('9 by you, 14 by others')).toBeInTheDocument()

    expect(screen.getByText('Submitted')).toBeInTheDocument()
    expect(screen.getByText('33')).toBeInTheDocument()
    expect(screen.getByText('last 7 days: 11')).toBeInTheDocument()

    expect(screen.getByText('Rejected')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('low quality extractions')).toBeInTheDocument()
  })

  it('renders five loading skeleton cards while stats are pending', () => {
    renderCards({
      isLoading: true,
      stats: undefined,
    })

    expect(screen.getAllByTestId('inventory-stats-card-skeleton')).toHaveLength(5)
  })

  it('renders a graceful empty state when stats fail to load', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    renderCards({
      errorMessage: 'Stats service unavailable',
      onRetry,
      stats: undefined,
    })

    expect(screen.getByText('Inventory stats unavailable')).toBeInTheDocument()
    expect(
      screen.getByText('Status counts are temporarily unavailable. Session filters still work normally.')
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
