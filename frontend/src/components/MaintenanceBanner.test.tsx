import { ThemeProvider } from '@mui/material/styles'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import MaintenanceBanner from './MaintenanceBanner'

function renderBanner() {
  return render(
    <ThemeProvider theme={theme}>
      <MaintenanceBanner />
    </ThemeProvider>,
  )
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

describe('MaintenanceBanner', () => {
  beforeEach(() => {
    vi.mocked(global.fetch).mockReset()
  })

  it('shows active maintenance messaging and lets users dismiss it', async () => {
    vi.mocked(global.fetch).mockResolvedValue(jsonResponse({
      active: true,
      message: 'Database maintenance starts at 22:00 UTC.',
    }))

    renderBanner()

    expect(await screen.findByRole('status')).toBeInTheDocument()
    expect(screen.getByText('Scheduled Maintenance')).toBeInTheDocument()
    expect(screen.getByText('Database maintenance starts at 22:00 UTC.')).toBeInTheDocument()
    expect(global.fetch).toHaveBeenCalledWith('/api/maintenance/message')

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })

  it('stays hidden when there is no active maintenance message', async () => {
    vi.mocked(global.fetch).mockResolvedValue(jsonResponse({
      active: false,
      message: '',
    }))

    renderBanner()

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/maintenance/message')
    })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
