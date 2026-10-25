import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useWorkspaceReviewCompletion } from './useWorkspaceReviewCompletion'
import { fetchActiveReviewOperations } from '../services/validationHistoryService'
vi.mock('../services/validationHistoryService', () => ({ fetchActiveReviewOperations: vi.fn() }))
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
it('refreshes the workspace after a background round finishes without any open control', async () => {
  const operation = { operation_id: 'round', session_id: 'workspace', state: 'running', artifact_count: 1, outcome: null }
  vi.mocked(fetchActiveReviewOperations).mockResolvedValueOnce([operation]).mockResolvedValue([])
  const refresh = vi.fn().mockResolvedValue(undefined)
  const hook = renderHook(() => useWorkspaceReviewCompletion('workspace', refresh))
  await waitFor(() => expect(fetchActiveReviewOperations).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1), { timeout: 2500 })
  hook.unmount()
})
it('observes a submitted round that finishes between polls', async () => {
  vi.mocked(fetchActiveReviewOperations).mockResolvedValue([])
  const refresh = vi.fn().mockResolvedValue(undefined)
  const hook = renderHook(() => useWorkspaceReviewCompletion('workspace', refresh))
  await waitFor(() => expect(fetchActiveReviewOperations).toHaveBeenCalledTimes(1))
  act(() => window.dispatchEvent(new CustomEvent('workspace-review-started', { detail: {
    operation_id: 'fast', session_id: 'workspace', state: 'queued', artifact_count: 1, outcome: null,
  } })))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1), { timeout: 2500 })
  hook.unmount()
})
