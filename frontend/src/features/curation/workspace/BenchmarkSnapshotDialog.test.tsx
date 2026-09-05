import { act, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CurationWorkspaceRequestError } from '@/features/curation/services/curationWorkspaceService'
import theme from '@/theme'
import BenchmarkSnapshotDialog from './BenchmarkSnapshotDialog'

const serviceMocks = vi.hoisted(() => ({
  createCurationBenchmarkSnapshot: vi.fn(),
  fetchBenchmarkDestinations: vi.fn(),
  handoffCurationBenchmarkSnapshot: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', async () => {
  const actual = await vi.importActual<
    typeof import('@/features/curation/services/curationWorkspaceService')
  >('@/features/curation/services/curationWorkspaceService')
  return {
    ...actual,
    createCurationBenchmarkSnapshot: serviceMocks.createCurationBenchmarkSnapshot,
    fetchBenchmarkDestinations: serviceMocks.fetchBenchmarkDestinations,
    handoffCurationBenchmarkSnapshot: serviceMocks.handoffCurationBenchmarkSnapshot,
  }
})

const snapshot = {
  snapshot_id: 'snapshot-1',
  schema_version: 'curation-benchmark-snapshot/v1' as const,
  envelope_revision: 4,
  envelope_digest: 'sha256:digest',
  download_path: '/api/curation-workspace/benchmark-snapshots/snapshot-1/download',
}

function dialogElement(overrides: Partial<ComponentProps<typeof BenchmarkSnapshotDialog>> = {}) {
  return (
    <ThemeProvider theme={theme}>
      <BenchmarkSnapshotDialog
        envelopes={[{ envelopeId: 'env-1', revision: 4 }]}
        hasUnsavedChanges={false}
        onClose={vi.fn()}
        open
        sessionId="session-1"
        {...overrides}
      />
    </ThemeProvider>
  )
}

function renderDialog(overrides: Partial<ComponentProps<typeof BenchmarkSnapshotDialog>> = {}) {
  return render(dialogElement(overrides))
}

describe('BenchmarkSnapshotDialog', () => {
  beforeEach(() => {
    serviceMocks.createCurationBenchmarkSnapshot.mockReset()
    serviceMocks.fetchBenchmarkDestinations.mockReset()
    serviceMocks.handoffCurationBenchmarkSnapshot.mockReset()
    serviceMocks.fetchBenchmarkDestinations.mockResolvedValue({
      destinations: [{ destination_id: 'portal', label: 'Alliance Benchmark' }],
    })
    serviceMocks.createCurationBenchmarkSnapshot.mockResolvedValue(snapshot)
  })

  it('sends the displayed persisted revision and reveals destination navigation only on success', async () => {
    serviceMocks.handoffCurationBenchmarkSnapshot.mockResolvedValue({
      handoff_id: 'handoff-1',
      snapshot_id: 'snapshot-1',
      destination_id: 'portal',
      status: 'succeeded',
      redirect_path: 'https://portal.example/comparisons/opaque',
    })
    renderDialog()

    expect(screen.getByRole('dialog', { name: 'Send snapshot to Benchmark' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open Benchmark' })).not.toBeInTheDocument()
    await screen.findByRole('combobox', { name: 'Benchmark destination' })
    await userEvent.click(screen.getByRole('button', { name: 'Send snapshot' }))

    await waitFor(() => {
      expect(serviceMocks.createCurationBenchmarkSnapshot).toHaveBeenCalledWith(
        'session-1',
        'env-1',
        { expected_revision: 4 },
      )
    })
    expect(serviceMocks.handoffCurationBenchmarkSnapshot).toHaveBeenCalledWith(
      'snapshot-1',
      { destination_id: 'portal' },
    )
    expect(await screen.findByText(/Snapshot delivery succeeded/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download benchmark bundle JSON' })).toHaveAttribute(
      'href',
      snapshot.download_path,
    )
    expect(screen.getByRole('link', { name: 'Open Benchmark' })).toHaveAttribute(
      'href',
      'https://portal.example/comparisons/opaque',
    )
    expect(screen.getByRole('link', { name: 'Open Benchmark' })).toHaveAttribute(
      'rel',
      'noopener noreferrer',
    )
  })

  it('preserves an unknown handoff for download without navigation or resend', async () => {
    serviceMocks.handoffCurationBenchmarkSnapshot.mockResolvedValue({
      handoff_id: 'handoff-1',
      snapshot_id: 'snapshot-1',
      destination_id: 'portal',
      status: 'unknown',
    })
    renderDialog()

    await screen.findByRole('combobox', { name: 'Benchmark destination' })
    await userEvent.click(screen.getByRole('button', { name: 'Send snapshot' }))

    expect(await screen.findByText(/Delivery could not be confirmed/)).toBeInTheDocument()
    expect(screen.getByText(/it was not sent again automatically/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download benchmark bundle JSON' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open Benchmark' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send snapshot' })).toBeDisabled()
    expect(serviceMocks.handoffCurationBenchmarkSnapshot).toHaveBeenCalledTimes(1)
  })

  it('requires unsaved changes to be saved instead of exporting another revision', async () => {
    renderDialog({ hasUnsavedChanges: true })

    expect(await screen.findByText(/Save the current changes before creating a snapshot/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send snapshot' })).toBeDisabled()
    expect(serviceMocks.createCurationBenchmarkSnapshot).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Prepare benchmark bundle JSON for download' })).toBeDisabled()
  })

  it.each(['empty', 'unavailable'])('allows manual download with %s destinations', async (state) => {
    if (state === 'empty') {
      serviceMocks.fetchBenchmarkDestinations.mockResolvedValueOnce({ destinations: [] })
    } else {
      serviceMocks.fetchBenchmarkDestinations.mockRejectedValueOnce(new Error('Destination unavailable'))
    }
    renderDialog()
    await screen.findByText(/No benchmark destination is currently available/)
    await userEvent.click(screen.getByRole('button', { name: 'Prepare benchmark bundle JSON for download' }))

    expect(await screen.findByRole('link', { name: 'Download benchmark bundle JSON' })).toHaveAttribute('href', snapshot.download_path)
    expect(serviceMocks.createCurationBenchmarkSnapshot).toHaveBeenCalledWith('session-1', 'env-1', { expected_revision: 4 })
    expect(serviceMocks.handoffCurationBenchmarkSnapshot).not.toHaveBeenCalled()
  })

  it('retains unknown delivery and download across equivalent refresh and close/reopen', async () => {
    serviceMocks.handoffCurationBenchmarkSnapshot.mockResolvedValue({
      handoff_id: 'handoff-1', snapshot_id: 'snapshot-1', destination_id: 'portal', status: 'unknown',
    })
    const view = renderDialog()
    await screen.findByRole('combobox', { name: 'Benchmark destination' })
    await userEvent.click(screen.getByRole('button', { name: 'Send snapshot' }))
    await screen.findByText(/Delivery could not be confirmed/)

    view.rerender(dialogElement())
    expect(screen.getByText(/Delivery could not be confirmed/)).toBeInTheDocument()
    expect(serviceMocks.fetchBenchmarkDestinations).toHaveBeenCalledTimes(1)
    view.rerender(dialogElement({ open: false }))
    view.rerender(dialogElement())
    await screen.findByRole('combobox', { name: 'Benchmark destination' })
    expect(screen.getByRole('link', { name: 'Download benchmark bundle JSON' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send snapshot' })).toBeDisabled()
    expect(serviceMocks.handoffCurationBenchmarkSnapshot).toHaveBeenCalledTimes(1)
  })

  it('disables selectors during send and isolates a late completion from a newer revision', async () => {
    let finishCreate!: (value: typeof snapshot) => void
    serviceMocks.createCurationBenchmarkSnapshot.mockImplementationOnce(() => new Promise((resolve) => { finishCreate = resolve }))
    serviceMocks.handoffCurationBenchmarkSnapshot.mockResolvedValue({
      handoff_id: 'handoff-1', snapshot_id: 'snapshot-1', destination_id: 'portal', status: 'unknown',
    })
    const view = renderDialog()
    await screen.findByRole('combobox', { name: 'Benchmark destination' })
    await userEvent.click(screen.getByRole('button', { name: 'Send snapshot' }))
    expect(screen.getByRole('combobox', { name: 'Persisted envelope' })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('combobox', { name: 'Benchmark destination' })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Preparing snapshot')

    view.rerender(dialogElement({ envelopes: [{ envelopeId: 'env-1', revision: 5 }] }))
    await act(async () => { finishCreate(snapshot) })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send snapshot' })).toBeEnabled())
    expect(screen.queryByRole('link', { name: 'Download benchmark bundle JSON' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Delivery could not be confirmed/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Send snapshot' }))
    expect(serviceMocks.createCurationBenchmarkSnapshot).toHaveBeenLastCalledWith('session-1', 'env-1', { expected_revision: 5 })
  })

  it('explains stale revision and authorization failures', async () => {
    serviceMocks.createCurationBenchmarkSnapshot.mockRejectedValueOnce(
      new CurationWorkspaceRequestError(409, 'conflict'),
    )
    const staleDialog = renderDialog()
    await screen.findByRole('combobox', { name: 'Benchmark destination' })
    await userEvent.click(screen.getByRole('button', { name: 'Send snapshot' }))
    expect(await screen.findByText(/revision is stale/)).toBeInTheDocument()
    staleDialog.unmount()

    serviceMocks.fetchBenchmarkDestinations.mockRejectedValueOnce(
      new CurationWorkspaceRequestError(403, 'forbidden'),
    )
    renderDialog()
    expect(await screen.findByText(/not authorized to send this snapshot/)).toBeInTheDocument()
  })

  it('shows destination-unavailable and failed-delivery recovery states', async () => {
    serviceMocks.fetchBenchmarkDestinations.mockResolvedValueOnce({ destinations: [] })
    const first = renderDialog()
    expect(await screen.findByText(/No benchmark destination is currently available/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send snapshot' })).toBeDisabled()
    first.unmount()

    serviceMocks.handoffCurationBenchmarkSnapshot.mockResolvedValueOnce({
      handoff_id: 'handoff-1',
      snapshot_id: 'snapshot-1',
      destination_id: 'portal',
      status: 'failed',
    })
    renderDialog()
    await screen.findByRole('combobox', { name: 'Benchmark destination' })
    await userEvent.click(screen.getByRole('button', { name: 'Send snapshot' }))
    expect(await screen.findByText(/Snapshot delivery failed/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download benchmark bundle JSON' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open Benchmark' })).not.toBeInTheDocument()
  })
})
