import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@/test/test-utils'

import AuthoritativeReviewAndCurateButton, {
  LEGACY_REVIEW_SESSION_POLICY,
} from './AuthoritativeReviewAndCurateButton'

const mockNavigate = vi.fn()
const openCurationWorkspaceMock = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace',
  )
  return {
    ...actual,
    openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
  }
})

describe('AuthoritativeReviewAndCurateButton', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    openCurationWorkspaceMock.mockResolvedValue('opened-session')
  })

  it('documents the only supported legacy reconstruction policy', () => {
    expect(LEGACY_REVIEW_SESSION_POLICY).toBe(
      'reconstruct_only_when_authoritative_ids_are_missing',
    )
  })

  it('disables authoritative zero-session results without reconstructing', () => {
    render(
      <AuthoritativeReviewAndCurateButton
        authoritativeReviewSessionIds={[]}
        documentId="doc-1"
      />,
    )

    const button = screen.getByRole('button', { name: /review & curate/i })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(openCurationWorkspaceMock).not.toHaveBeenCalled()
  })

  it('opens the sole authoritative session directly', async () => {
    render(
      <AuthoritativeReviewAndCurateButton
        authoritativeReviewSessionIds={['review-exact']}
        documentId="doc-1"
        flowRunId="flow-run-1"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))
    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: 'review-exact' }),
      )
    })
  })

  it('requires an explicit adapter/session choice for multiple authoritative sessions', async () => {
    render(
      <AuthoritativeReviewAndCurateButton
        authoritativeReviewSessionIds={['review-gene', 'review-allele']}
        adapterKeys={['gene', 'allele']}
        documentId="doc-1"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))
    expect(openCurationWorkspaceMock).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: /allele — review-allele/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: 'review-allele' }),
      )
    })
  })

  it('uses reconstruction only when the authoritative field is absent', async () => {
    render(<AuthoritativeReviewAndCurateButton documentId="legacy-doc" />)

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i }))
    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({ documentId: 'legacy-doc', sessionId: undefined }),
      )
    })
  })
})
