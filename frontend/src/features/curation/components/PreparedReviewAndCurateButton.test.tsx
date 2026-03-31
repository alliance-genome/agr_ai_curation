import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@/test/test-utils'

import PreparedReviewAndCurateButton from './PreparedReviewAndCurateButton'

const mockNavigate = vi.fn()
const openCurationWorkspaceMock = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace'
  )

  return {
    ...actual,
    openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
  }
})

describe('PreparedReviewAndCurateButton', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
  })

  it('renders immediately without firing availability probes', () => {
    render(
      <PreparedReviewAndCurateButton
        documentId="doc-1"
        iconOnly={true}
      />
    )

    expect(screen.getByRole('button', { name: /review & curate/i })).toBeInTheDocument()
  })

  it('renders with an existing session ID and opens that session on click', async () => {
    openCurationWorkspaceMock.mockResolvedValue('session-existing')

    render(
      <PreparedReviewAndCurateButton
        sessionId="session-existing"
        documentId="doc-1"
        flowRunId="flow-1"
        adapterKeys={[' gene ', 'gene', '']}
        iconOnly={true}
      />
    )

    const button = screen.getByRole('button', { name: /review & curate/i })
    fireEvent.click(button)

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'session-existing',
          documentId: 'doc-1',
          flowRunId: 'flow-1',
          adapterKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })
  })

  it('renders without a session ID and resolves on click', async () => {
    openCurationWorkspaceMock.mockResolvedValue('session-bootstrapped')

    render(<PreparedReviewAndCurateButton documentId="doc-1" iconOnly={true} />)

    const button = screen.getByRole('button', { name: /review & curate/i })
    fireEvent.click(button)

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          documentId: 'doc-1',
          navigate: mockNavigate,
        })
      )
    })
  })

  it('normalizes adapter keys', () => {
    render(
      <PreparedReviewAndCurateButton
        documentId="doc-1"
        adapterKeys={[' gene ', 'gene', '', ' allele ']}
        iconOnly={true}
      />
    )

    expect(screen.getByRole('button', { name: /review & curate/i })).toBeInTheDocument()
  })

  it('shows a loading state while opening the workspace', async () => {
    let resolveOpen: (value: string) => void
    openCurationWorkspaceMock.mockImplementation(
      () => new Promise<string>((resolve) => { resolveOpen = resolve })
    )

    render(<PreparedReviewAndCurateButton documentId="doc-1" iconOnly={true} />)

    const button = screen.getByRole('button', { name: /review & curate/i })
    fireEvent.click(button)

    // Button should be disabled while opening
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /review & curate/i })).toBeDisabled()
    })

    // Resolve the open to clean up
    resolveOpen!('session-1')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /review & curate/i })).not.toBeDisabled()
    })
  })

  it('shows an error toast when opening fails instead of hiding the button', async () => {
    openCurationWorkspaceMock.mockRejectedValue(new Error('Backend unavailable'))

    const toastEvents: CustomEvent[] = []
    const listener = (e: Event) => toastEvents.push(e as CustomEvent)
    window.addEventListener('agr-global-toast', listener)

    render(<PreparedReviewAndCurateButton documentId="doc-1" iconOnly={true} />)

    const button = screen.getByRole('button', { name: /review & curate/i })
    fireEvent.click(button)

    await waitFor(() => {
      expect(toastEvents.length).toBe(1)
      expect(toastEvents[0].detail).toEqual(
        expect.objectContaining({
          message: expect.stringContaining('Backend unavailable'),
          severity: 'error',
        })
      )
    })

    // Button remains visible and re-enabled after error
    expect(screen.getByRole('button', { name: /review & curate/i })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /review & curate/i })).not.toBeDisabled()
    })

    window.removeEventListener('agr-global-toast', listener)
  })
})
