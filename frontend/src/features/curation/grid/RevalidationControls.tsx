import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Stack, TextField, Typography } from '@mui/material'
import { controlReview, fetchCandidateReviewOperation, fetchSessionReviewOperation, fetchReviewOperation, requestReview, type ReviewOperation } from '../services/validationHistoryService'

export default function RevalidationControls({ sessionId, candidateId, onChanged, beforeChange }: {
  sessionId: string; candidateId?: string; onChanged: () => void | Promise<void>; beforeChange?: () => Promise<void>
}) {
  const [guidance, setGuidance] = useState('')
  const [operation, setOperation] = useState<ReviewOperation | null>(null)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const request = useRef<{ id: string; guidance: string | null } | null>(null)
  const active = operation && ['queued', 'preparing', 'running'].includes(operation.state)
  useEffect(() => {
    const controller = new AbortController()
    setLoaded(false)
    setOperation(null)
    request.current = null
    const lookup = candidateId ? fetchCandidateReviewOperation(candidateId, controller.signal) : fetchSessionReviewOperation(sessionId, controller.signal)
    lookup.then(saved => {
      if (!controller.signal.aborted) { setOperation(saved); setLoaded(true) }
    }).catch(failure => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Unable to read saved revalidation status')
    })
    return () => controller.abort()
  }, [candidateId, sessionId])
  useEffect(() => {
    if (!active || !operation) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await fetchReviewOperation(operation.operation_id, controller.signal)
        if (controller.signal.aborted) return
        setOperation(next)
        if (['queued', 'preparing', 'running'].includes(next.state)) timer = setTimeout(() => void poll(), 1000)
        else await onChanged()
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Unable to read operation status')
      }
    }
    timer = setTimeout(() => void poll(), 1000)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [active, operation?.operation_id, onChanged])
  async function start() {
    setBusy(true); setError(null)
    try {
      if (!request.current) {
        await beforeChange?.()
        request.current = { id: crypto.randomUUID(), guidance: guidance.trim() || null }
      }
      const saved = await requestReview(sessionId, candidateId ? [candidateId] : [], request.current.guidance, request.current.id)
      request.current = null
      setOperation(saved)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to start revalidation') }
    finally { setBusy(false) }
  }
  async function control(action: 'cancel' | 'resume') {
    if (!operation) return
    setBusy(true); setError(null)
    try { setOperation(await controlReview(operation.operation_id, action)); onChanged() }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to update revalidation') }
    finally { setBusy(false) }
  }
  return <Stack spacing={1}>
    <Typography variant="subtitle2">Request another validation round</Typography>
    <Typography variant="body2">An accepted answer may replace the current value. Edits you make after this request remain protected.</Typography>
    <TextField multiline label="Instructions (optional)" value={guidance} disabled={busy || Boolean(active) || request.current !== null} onChange={event => setGuidance(event.target.value)} />
    {error && <Alert severity="error">{error}</Alert>}
    {operation && <Typography role="status">Revalidation: {operation.state.replaceAll('_', ' ')}</Typography>}
    <Stack direction="row" spacing={1}>
      <Button disabled={!loaded || busy || Boolean(active)} onClick={() => void start()}>{request.current ? 'Retry this request' : candidateId ? 'Revalidate record' : 'Revalidate all records'}</Button>
      {active && <Button disabled={busy} onClick={() => void control('cancel')}>Stop revalidation</Button>}
      {operation?.state === 'needs_review' && <Button disabled={busy} onClick={() => void control('resume')}>Continue existing round</Button>}
    </Stack>
  </Stack>
}
