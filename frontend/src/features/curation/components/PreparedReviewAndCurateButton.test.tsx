import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@/test/test-utils'

import PreparedReviewAndCurateButton from './PreparedReviewAndCurateButton'

const mockNavigate = vi.fn()
const openCurationWorkspaceMock = vi.fn()
const getCurationWorkspaceLaunchAvailabilityMock = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('@/features/curation/navigation/openCurationWorkspace', () => ({
  getCurationWorkspaceLaunchAvailability: (options: unknown) =>
    getCurationWorkspaceLaunchAvailabilityMock(options),
  openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
}))

describe('PreparedReviewAndCurateButton', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    getCurationWorkspaceLaunchAvailabilityMock.mockReset()
  })

  it('renders when an existing session is available and opens that session directly', async () => {
    getCurationWorkspaceLaunchAvailabilityMock.mockResolvedValue({
      existingSessionId: 'session-existing',
      canBootstrap: true,
    })
    openCurationWorkspaceMock.mockResolvedValue('session-existing')

    render(
      <PreparedReviewAndCurateButton
        documentId="doc-1"
        flowRunId="flow-1"
        iconOnly={true}
      />
    )

    const button = await screen.findByRole('button', { name: /review & curate/i })
    expect(getCurationWorkspaceLaunchAvailabilityMock).toHaveBeenCalledWith({
      sessionId: undefined,
      documentId: 'doc-1',
      flowRunId: 'flow-1',
      originSessionId: undefined,
      adapterKeys: [],
      profileKeys: [],
      domainKeys: [],
    })

    fireEvent.click(button)

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

  it('renders when no session exists yet but bootstrap is available', async () => {
    getCurationWorkspaceLaunchAvailabilityMock.mockResolvedValue({
      existingSessionId: null,
      canBootstrap: true,
    })
    openCurationWorkspaceMock.mockResolvedValue('session-bootstrapped')

    render(<PreparedReviewAndCurateButton documentId="doc-1" iconOnly={true} />)

    fireEvent.click(await screen.findByRole('button', { name: /review & curate/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: null,
          documentId: 'doc-1',
          navigate: mockNavigate,
        })
      )
    })
  })

  it('does not render when no existing session is available', async () => {
    getCurationWorkspaceLaunchAvailabilityMock.mockResolvedValue({
      existingSessionId: null,
      canBootstrap: false,
    })

    render(<PreparedReviewAndCurateButton documentId="doc-1" iconOnly={true} />)

    await waitFor(() => {
      expect(getCurationWorkspaceLaunchAvailabilityMock).toHaveBeenCalledTimes(1)
    })

    expect(screen.queryByRole('button', { name: /review & curate/i })).not.toBeInTheDocument()
  })
})
