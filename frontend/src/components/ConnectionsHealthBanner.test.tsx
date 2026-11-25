import { ThemeProvider } from '@mui/material/styles'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchConnectionsHealth,
  type ConnectionsHealthResponse,
} from '@/services/adminService'
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
    observed_at: '2026-11-25T00:00:00+00:00',
    status,
    required_healthy: status !== 'unhealthy',
    unhealthy_services:
      status === 'healthy'
        ? []
        : [
            {
              description:
                status === 'unhealthy'
                  ? 'Vector database for document search and RAG'
                  : 'LLM observability and tracing',
              required: status === 'unhealthy',
            },
          ],
    ...overrides,
  }
}

describe('ConnectionsHealthBanner', () => {
  beforeEach(() => {
    mockUseConnectionsHealth.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('polls the public minimal status endpoint instead of the admin endpoint', async () => {
    const payload = buildHealth('degraded')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => payload,
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchConnectionsHealth()

    expect(fetchMock).toHaveBeenCalledWith('/api/health/connections')
    expect(result).toEqual(payload)
  })

  it('throws startup message on 503 from the public minimal status endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchConnectionsHealth()).rejects.toThrow('Service is starting up')
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

  it('shows degraded service descriptions and lets users dismiss that status level', async () => {
    mockUseConnectionsHealth.mockReturnValue({
      data: buildHealth('degraded'),
      isLoading: false,
    })

    renderBanner()

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('Service Degraded')).toBeInTheDocument()
    expect(screen.getByText('Some optional services are unavailable. Core features still work.')).toBeInTheDocument()
    expect(screen.getByText('Affected services:')).toBeInTheDocument()
    expect(screen.getByText('LLM observability and tracing')).toBeInTheDocument()

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
    expect(screen.getByText('Vector database for document search and RAG (required)')).toBeInTheDocument()
  })

  it('truncates lists longer than four affected services', () => {
    mockUseConnectionsHealth.mockReturnValue({
      data: buildHealth('degraded', {
        unhealthy_services: [
          { description: 'Service One', required: false },
          { description: 'Service Two', required: false },
          { description: 'Service Three', required: false },
          { description: 'Service Four', required: false },
          { description: 'Service Five', required: false },
        ],
      }),
      isLoading: false,
    })

    renderBanner()

    expect(screen.getByText('Service Four')).toBeInTheDocument()
    expect(screen.queryByText('Service Five')).not.toBeInTheDocument()
    expect(screen.getByText('...and 1 more')).toBeInTheDocument()
  })
})
