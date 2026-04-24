import { ThemeProvider } from '@mui/material/styles'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConnectionsHealthResponse } from '@/services/adminService'
import theme from '@/theme'
import ConnectionsHealthBanner from './ConnectionsHealthBanner'

const mockUseConnectionsHealth = vi.hoisted(() => vi.fn())

vi.mock('../services/adminService', async () => {
  const actual = await vi.importActual<typeof import('../services/adminService')>('../services/adminService')

  return {
    ...actual,
    useConnectionsHealth: () => mockUseConnectionsHealth(),
  }
})

function renderBanner() {
  return render(
    <ThemeProvider theme={theme}>
      <ConnectionsHealthBanner />
    </ThemeProvider>,
  )
}

function buildHealth(
  status: ConnectionsHealthResponse['status'],
  overrides: Partial<ConnectionsHealthResponse> = {},
): ConnectionsHealthResponse {
  return {
    status,
    total_services: 1,
    healthy_count: status === 'healthy' ? 1 : 0,
    unhealthy_count: status === 'healthy' ? 0 : 1,
    unknown_count: 0,
    required_healthy: status !== 'unhealthy',
    services: {
      weaviate: {
        service_id: 'weaviate',
        description: 'Weaviate',
        url: 'http://weaviate:8080',
        required: status === 'unhealthy',
        is_healthy: status === 'healthy',
        last_error: status === 'healthy' ? null : 'Connection refused',
      },
    },
    ...overrides,
  }
}

describe('ConnectionsHealthBanner', () => {
  beforeEach(() => {
    mockUseConnectionsHealth.mockReset()
  })

  it('stays hidden while health is loading or healthy', () => {
    mockUseConnectionsHealth.mockReturnValue({ data: undefined, isLoading: true })

    const view = renderBanner()

    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    mockUseConnectionsHealth.mockReturnValue({ data: buildHealth('healthy'), isLoading: false })
    view.rerender(
      <ThemeProvider theme={theme}>
        <ConnectionsHealthBanner />
      </ThemeProvider>,
    )

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows degraded services and lets users dismiss that status level', async () => {
    mockUseConnectionsHealth.mockReturnValue({
      data: buildHealth('degraded'),
      isLoading: false,
    })

    renderBanner()

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('Service Degraded')).toBeInTheDocument()
    expect(screen.getByText('Some optional services are unavailable. Core features still work.')).toBeInTheDocument()
    expect(screen.getByText('Affected services:')).toBeInTheDocument()
    expect(screen.getByText(/weaviate/)).toBeInTheDocument()
    expect(screen.getByText(/Connection refused/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })

  it('reappears when the dismissed degraded status worsens to unhealthy', () => {
    mockUseConnectionsHealth.mockReturnValue({
      data: buildHealth('degraded'),
      isLoading: false,
    })

    const view = renderBanner()

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    mockUseConnectionsHealth.mockReturnValue({
      data: buildHealth('unhealthy'),
      isLoading: false,
    })
    view.rerender(
      <ThemeProvider theme={theme}>
        <ConnectionsHealthBanner />
      </ThemeProvider>,
    )

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('Service Unavailable')).toBeInTheDocument()
    expect(screen.getByText('Required services are unavailable. Some features may not work.')).toBeInTheDocument()
    expect(screen.getByText(/weaviate \(required\)/)).toBeInTheDocument()
  })
})
