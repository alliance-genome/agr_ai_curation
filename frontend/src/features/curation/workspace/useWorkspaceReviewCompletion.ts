import { useEffect, useRef, useState } from 'react'
import { fetchActiveReviewOperations, type ReviewOperation } from '../services/validationHistoryService'

/** Keep observing background rounds after their Details/dialog is closed. */
export function useWorkspaceReviewCompletion(sessionId: string, onCompleted: () => Promise<void>) {
  const refresh = useRef(onCompleted)
  refresh.current = onCompleted
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    let pending = new Set<string>()
    let timer: ReturnType<typeof setTimeout>
    const started = (event: Event) => {
      const operation = (event as CustomEvent<ReviewOperation>).detail
      if (operation.session_id === sessionId) pending.add(operation.operation_id)
    }
    window.addEventListener('workspace-review-started', started)
    const poll = async () => {
      const observing = new Set(pending)
      try {
        const operations = await fetchActiveReviewOperations(sessionId, controller.signal)
        if (controller.signal.aborted) return
        const active = new Set(operations.map(operation => operation.operation_id))
        if ([...observing].some(id => !active.has(id))) await refresh.current()
        pending = new Set([...active, ...[...pending].filter(id => !observing.has(id))])
        setError(null)
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Unable to observe background validation')
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 1000)
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener('workspace-review-started', started) }
  }, [sessionId])
  return error
}
