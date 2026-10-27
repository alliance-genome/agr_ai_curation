import { useRef, useState } from 'react'
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Stack, TextField, Typography } from '@mui/material'
import type { RetiredRecord } from '../types'
import { fetchRecordHistory, restoreRecord } from '../services/validationHistoryService'

function SavedDetails({ record, reference, label }: { record: RetiredRecord; reference: RetiredRecord['original']; label: string }) {
  const [content, setContent] = useState('')
  const [offset, setOffset] = useState<number | null>(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function load() {
    if (busy || offset === null) return
    setBusy(true); setError(null)
    try {
      const page = await fetchRecordHistory(record.envelope_id, reference, offset)
      setContent(previous => previous + page.content); setOffset(page.next_offset)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to read history') }
    finally { setBusy(false) }
  }
  return <Box>
    {error && <Alert severity="error">{error}</Alert>}
    {content && <Box component="pre" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{content}</Box>}
    {offset !== null && <Button disabled={busy} onClick={() => void load()}>{content ? `Read more: ${label}` : label}</Button>}
  </Box>
}
function RetiredItem({ record, beforeChange, onChanged }: { record: RetiredRecord; beforeChange: () => Promise<void>; onChanged: () => Promise<void> }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pending = useRef<{ id: string; reason: string } | null>(null)
  async function restore() {
    if (busy) return
    setBusy(true); setError(null)
    try {
      if (!pending.current) {
        await beforeChange()
        pending.current = { id: crypto.randomUUID(), reason }
      }
      await restoreRecord(record.envelope_id, record.target_id, record.version, pending.current.reason, pending.current.id)
      await onChanged()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to restore record') }
    finally { setBusy(false) }
  }
  return <Stack spacing={1} sx={{ py: 1 }}>
    <Typography variant="subtitle2">Retired record</Typography>
    <Typography>{record.reason}</Typography>
    <SavedDetails record={record} reference={record.original} label="Original extraction" />
    <SavedDetails record={record} reference={record.proposal} label="Extractor recommendation and evidence" />
    <SavedDetails record={record} reference={record.retirement} label="Supervisor decision" />
    <TextField label="Reason for restoring" value={reason} disabled={busy || pending.current !== null} onChange={event => setReason(event.target.value)} />
    {error && <Alert severity="error">{error}</Alert>}
    {error && <Button disabled={busy} onClick={() => { void onChanged().then(() => { pending.current = null; setError(null) }).catch(failure => setError(failure instanceof Error ? failure.message : 'Unable to refresh history')) }}>Refresh history</Button>}
    <Button disabled={busy || !reason.trim()} onClick={() => void restore()}>Restore for review</Button>
  </Stack>
}
export default function RetiredRecordsPanel({ records, beforeChange, onChanged }: { records: RetiredRecord[]; beforeChange: () => Promise<void>; onChanged: () => Promise<void> }) {
  if (!records.length) return null
  return <Accordion>
    <AccordionSummary>Retired records ({records.length})</AccordionSummary>
    <AccordionDetails>
      <Typography>These records are excluded from current results. Their extraction and review history remain available. Restoring a record requires review before export; it does not start another model run.</Typography>
      {records.map(record => <RetiredItem key={`${record.target_id}:${record.version}`} record={record} beforeChange={beforeChange} onChanged={onChanged} />)}
    </AccordionDetails>
  </Accordion>
}
