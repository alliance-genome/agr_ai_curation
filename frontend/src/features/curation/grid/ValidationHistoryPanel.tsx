import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Box, Button, CircularProgress, Stack, TextField, Typography } from '@mui/material'
import {
  fetchValidationHistory, fetchValidationHistoryDetail, decideValidation,
  type HistoryEntry, type HistoryPage, type HistoryReference,
} from '../services/validationHistoryService'
import RevalidationControls from './RevalidationControls'

function ExactDetail({ candidateId, reference }: { candidateId: string; reference: HistoryReference }) {
  const [text, setText] = useState('')
  const [offset, setOffset] = useState<number | null>(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function load() {
    if (offset === null || busy) return
    setBusy(true)
    setError(null)
    try {
      const page = await fetchValidationHistoryDetail(candidateId, reference, offset)
      setText(previous => previous + page.content)
      setOffset(page.next_offset)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to read saved details') }
    finally { setBusy(false) }
  }
  return <Stack spacing={1}>
    {error && <Alert severity="error">{error}</Alert>}
    {text && <Box component="pre" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.75rem' }}>{text}</Box>}
    {offset !== null && <Button disabled={busy} onClick={() => void load()}>{busy ? 'Loading…' : text ? 'Load more saved details' : 'Read exact saved details'}</Button>}
  </Stack>
}
const eventLabels: Record<string, string> = {
  attempt: 'Validation requested', completion: 'Validator answer', decision: 'Review decision',
  curator_value: 'Curator edit', curator_revalidation_intent: 'Replacement authorized by curator',
  workspace_revalidation_request: 'Curator requested another round',
  extraction_followup_request: 'More evidence requested', extraction_contribution: 'Extractor follow-up',
  extraction_source_selection: 'Follow-up evidence selected', curator_revalidation_release: 'Accepted answer applied',
}

function HistoryItem({ candidateId, entry, episodeId, onChanged, beforeChange }: {
  candidateId: string; entry: HistoryEntry; episodeId: string | null; onChanged: () => void | Promise<void>; beforeChange?: () => Promise<void>
}) {
  const [expanded, setExpanded] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pending = useRef<{ id: string; action: 'accept' | 'reject'; reason: string } | null>(null)
  async function decide(action: 'accept' | 'reject') {
    if (!episodeId) return
    setBusy(true); setError(null)
    try {
      if (!pending.current) {
        await beforeChange?.()
        pending.current = { id: crypto.randomUUID(), action, reason }
      }
      await decideValidation(candidateId, episodeId, entry.choices, pending.current.action, pending.current.reason, pending.current.id)
      pending.current = null
      await onChanged()
    }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to save decision') }
    finally { setBusy(false) }
  }
  return <Box sx={{ borderBottom: 1, borderColor: 'divider', py: 1 }}>
    <Typography variant="subtitle2">{eventLabels[entry.kind]}</Typography>
    <Typography variant="caption">{new Date(entry.created_at).toLocaleString()}{entry.actor ? ` · ${entry.actor.type === 'curator' ? 'Curator' : 'Supervisor'}` : ''}</Typography>
    {entry.decisions.map((decision, index) => <Typography key={index} variant="body2">{decision.action}: {decision.reason}</Typography>)}
    {error && <Alert severity="error">{error}</Alert>}
    {episodeId && entry.choices.length > 0 && <Stack spacing={1}>
      <TextField label="Reason for your decision" disabled={busy || pending.current !== null} value={reason} onChange={event => setReason(event.target.value)} />
      <Stack direction="row"><Button disabled={busy || !reason.trim() || (pending.current !== null && pending.current.action !== 'accept')} onClick={() => void decide('accept')}>Accept this answer</Button>
        <Button disabled={busy || !reason.trim() || (pending.current !== null && pending.current.action !== 'reject')} onClick={() => void decide('reject')}>Reject this answer</Button></Stack>
    </Stack>}
    {entry.reason && <Typography variant="body2">{entry.reason}</Typography>}
    {entry.state && <Typography variant="body2">{entry.state.replaceAll('_', ' ')}</Typography>}
    <Button size="small" onClick={() => setExpanded(value => !value)}>{expanded ? 'Hide details' : 'Show details'}</Button>
    {expanded && <ExactDetail candidateId={candidateId} reference={entry} />}
  </Box>
}
export default function ValidationHistoryPanel({ candidateId, sessionId, onChanged, beforeChange }: { candidateId: string; sessionId?: string; onChanged?: () => void | Promise<void>; beforeChange?: () => Promise<void> }) {
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(async () => { setRevision(value => value + 1); await onChanged?.() }, [onChanged])
  const [page, setPage] = useState<HistoryPage | null>(null)
  const [entries, setEntries] = useState<HistoryEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setBusy(true)
    setPage(null)
    setEntries([])
    setError(null)
    fetchValidationHistory(candidateId, 0, controller.signal).then(result => {
      setPage(result); setEntries(result.entries)
    }).catch(failure => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Unable to load history')
    }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [candidateId, revision])
  async function more() {
    if (page?.next_after == null || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await fetchValidationHistory(candidateId, page.next_after)
      setPage(result); setEntries(previous => [...previous, ...result.entries])
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to load history') }
    finally { setBusy(false) }
  }
  return <Stack spacing={1} aria-label="Validation history">
    <Typography variant="h6">Validation history</Typography>
    <Typography variant="body2">Saved extraction, answers and decisions. Viewing history does not run validation.</Typography>
    {error && <Alert severity="error">{error}</Alert>}
    {page && <Box><Typography variant="subtitle2">Original extraction</Typography><ExactDetail key={page.baseline.sha256} candidateId={candidateId} reference={page.baseline} /></Box>}
    {sessionId && <RevalidationControls sessionId={sessionId} candidateId={candidateId} onChanged={refresh} beforeChange={beforeChange} />}
    {entries.map(entry => <HistoryItem key={entry.id} candidateId={candidateId} entry={entry} episodeId={page?.episode_id ?? null} onChanged={refresh} beforeChange={beforeChange} />)}
    {busy && <CircularProgress size={20} aria-label="Loading history" />}
    {page?.next_after != null && <Button disabled={busy} onClick={() => void more()}>Load more saved events</Button>}
  </Stack>
}
