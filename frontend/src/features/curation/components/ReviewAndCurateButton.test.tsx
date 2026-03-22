import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@/test/test-utils'

import ReviewAndCurateButton from './ReviewAndCurateButton'

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

vi.mock('@/features/curation/navigation/openCurationWorkspace', () => ({
  openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
}))

vi.mock('@/lib/globalNotifications', () => ({
  emitGlobalToast: (detail: unknown) => emitGlobalToastMock(detail),
}))

function createDeferredPromise<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void

  const promise = new Promise<T>((innerResolve, innerReject) => {
    resolve = innerResolve
    reject = innerReject
  })

  return { promise, resolve, reject }
}

describe('ReviewAndCurateButton', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    emitGlobalToastMock.mockReset()
  })

  it('shows a loading label while opening the workspace', async () => {
    const deferred = createDeferredPromise<string>()
    openCurationWorkspaceMock.mockReturnValueOnce(deferred.promise)

    render(<ReviewAndCurateButton documentId="doc-1" />)

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))

    expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
      expect.objectContaining({
        documentId: 'doc-1',
        navigate: mockNavigate,
      })
    )
    expect(screen.getByRole('button', { name: /opening/i })).toBeDisabled()

    deferred.resolve('session-1')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /review & curate/i })).toBeEnabled()
    })
  })

  it('shows an error toast when opening the workspace fails', async () => {
    openCurationWorkspaceMock.mockRejectedValueOnce(new Error('Workspace bootstrap failed'))

    render(<ReviewAndCurateButton documentId="doc-1" iconOnly={true} />)

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))

    await waitFor(() => {
      expect(emitGlobalToastMock).toHaveBeenCalledWith({
        message: 'Review & Curate failed: Workspace bootstrap failed',
        severity: 'error',
      })
    })
  })
})
