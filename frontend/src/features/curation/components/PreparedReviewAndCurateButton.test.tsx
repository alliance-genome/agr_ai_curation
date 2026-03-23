import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@/test/test-utils'

import PreparedReviewAndCurateButton from './PreparedReviewAndCurateButton'

const mockNavigate = vi.fn()
const openCurationWorkspaceMock = vi.fn()
const emitGlobalToastMock = vi.fn()

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

vi.mock('@/lib/globalNotifications', async () => {
  const actual = await vi.importActual<typeof import('@/lib/globalNotifications')>('@/lib/globalNotifications')
  return {
    ...actual,
    emitGlobalToast: (detail: unknown) => emitGlobalToastMock(detail),
  }
})

describe('PreparedReviewAndCurateButton', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    emitGlobalToastMock.mockReset()
  })

  it('always renders a button without firing any backend calls on mount', () => {
    render(
      <PreparedReviewAndCurateButton
        documentId="doc-1"
        iconOnly={true}
      />
    )

    expect(screen.getByRole('button', { name: /review & curate/i })).toBeInTheDocument()
    expect(openCurationWorkspaceMock).not.toHaveBeenCalled()
  })

  it('normalizes adapter, profile, and domain keys and opens workspace on click', async () => {
    openCurationWorkspaceMock.mockResolvedValue('session-1')

    render(
      <PreparedReviewAndCurateButton
        documentId="doc-1"
        flowRunId="flow-1"
        adapterKeys={[' gene ', 'gene', '']}
        profileKeys={[' primary ', 'primary']}
        domainKeys={[' disease ', 'disease']}
        iconOnly={true}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: null,
          documentId: 'doc-1',
          flowRunId: 'flow-1',
          adapterKeys: ['gene'],
          profileKeys: ['primary'],
          domainKeys: ['disease'],
          navigate: mockNavigate,
        })
      )
    })
  })

  it('passes through an existing sessionId when provided', async () => {
    openCurationWorkspaceMock.mockResolvedValue('session-existing')

    render(
      <PreparedReviewAndCurateButton
        sessionId="session-existing"
        documentId="doc-1"
        flowRunId="flow-1"
        iconOnly={true}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'session-existing',
          documentId: 'doc-1',
          flowRunId: 'flow-1',
          navigate: mockNavigate,
        })
      )
    })
  })

  it('shows a disabled loading state during click-time resolution', async () => {
    openCurationWorkspaceMock.mockReturnValue(new Promise(() => {}))

    render(
      <PreparedReviewAndCurateButton
        documentId="doc-1"
        iconOnly={true}
      />
    )

    const button = screen.getByRole('button', { name: /review & curate/i })
    expect(button).toBeEnabled()

    fireEvent.click(button)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /review & curate/i })).toBeDisabled()
      expect(screen.getByRole('progressbar')).toBeInTheDocument()
    })
  })

  it('shows an error toast and remains visible when click-time resolution fails', async () => {
    openCurationWorkspaceMock.mockRejectedValue(new Error('Backend unavailable'))

    render(
      <PreparedReviewAndCurateButton
        documentId="doc-1"
        iconOnly={true}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))

    await waitFor(() => {
      expect(emitGlobalToastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          message: expect.stringContaining('Backend unavailable'),
          severity: 'error',
        })
      )
    })

    // Button remains visible and re-enables after error — not hidden
    expect(screen.getByRole('button', { name: /review & curate/i })).toBeEnabled()
  })
})
