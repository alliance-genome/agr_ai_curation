import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Stack, Typography } from '@mui/material'

interface StoredDraft<T> { version: 1; updated: string; value: T }

/** One recovery slot per signed-in user and editor; never stores a server revision. */
export function useDraftRecovery<T>({ ownerId, kind, value, dirty, ready, restore }: {
  ownerId?: string; kind: 'agent' | 'flow'; value: T; dirty: boolean; ready: boolean
  restore: (value: T, stillCurrent: () => boolean) => void | Promise<void>
}) {
  const key = ownerId ? `agr-studio-draft:v1:${encodeURIComponent(ownerId)}:${kind}` : null
  const [pending, setPending] = useState<StoredDraft<T> | null>(null)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resuming, setResuming] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const live = useRef({ key, pending })
  live.current = { key, pending }
  const [kept, setKept] = useState(false)
  const firstOwner = useRef(ownerId)
  const expected = useRef<string | null>(null)
  const lastWritten = useRef<string | null>(null)
  useEffect(() => {
    setKept(false); setPending(null); setError(null); lastWritten.current = null
    if (!key) return
    if (firstOwner.current && firstOwner.current !== ownerId) {
      setLoadedKey(null); setError('Your account changed. Reopen this editor to start or recover a draft for this account.'); return
    }
    firstOwner.current = ownerId
    try {
      const raw = localStorage.getItem(key)
      expected.current = raw
      if (raw) {
        const entry = JSON.parse(raw) as StoredDraft<T>
        if (entry.version !== 1 || !entry.value || typeof entry.updated !== 'string') throw new Error()
        setPending(entry)
      }
    } catch { setError('The recovery draft could not be read. Your current editor is still available.') }
    setLoadedKey(key)
  }, [key, ownerId])
  const persist = useCallback(() => {
    if (!key || key !== loadedKey || pending || !ready || error) return
    try {
      const current = localStorage.getItem(key)
      if (current !== expected.current) {
        setError('Another tab changed the recovery draft. Your edits are still in this editor; use Save to keep them.'); return
      }
      if (!dirty) {
        localStorage.removeItem(key); expected.current = null; lastWritten.current = null; setKept(false); return
      }
      const serialized = JSON.stringify(value)
      if (serialized === lastWritten.current) return
      const raw = JSON.stringify({ version: 1, updated: new Date().toISOString(), value })
      localStorage.setItem(key, raw); expected.current = raw; lastWritten.current = serialized; setKept(true)
    } catch { setError('This browser could not keep a recovery draft. Keep this page open and use Save to preserve your work.') }
  }, [key, loadedKey, pending, ready, error, dirty, value])
  useEffect(() => { persist() }, [persist])
  useEffect(() => {
    window.addEventListener('pagehide', persist)
    return () => window.removeEventListener('pagehide', persist)
  }, [persist])
  const resume = async () => {
    if (!pending || !ready || resuming) return
    const stillCurrent = () => mounted.current && live.current.key === key && live.current.pending === pending
    setResuming(true)
    try {
      if (localStorage.getItem(key!) !== expected.current) throw new Error('Recovery changed')
      await restore(pending.value, stillCurrent)
      if (stillCurrent()) { setPending(null); setError(null) }
    }
    catch { if (stillCurrent()) setError('This recovery draft could not be restored. It has been kept on this device. Check that its source agent is still available, or discard this recovery draft.'); }
    finally { if (mounted.current) setResuming(false) }
  }
  const discard = () => {
    if (!key || resuming) return
    try {
      if (localStorage.getItem(key) !== expected.current) throw new Error()
      localStorage.removeItem(key); expected.current = null; lastWritten.current = null
      setPending(null); setKept(false); setError(null)
    } catch { setError('The recovery draft changed or could not be removed. Reload to review it.') }
  }
  return { pending: Boolean(pending), resume, discard, kept, error, resuming, ready: ready && !resuming }
}

export function DraftRecoveryNotice({ recovery }: { recovery: ReturnType<typeof useDraftRecovery> }) {
  if (recovery.pending) return <Alert severity="info">
    <Typography>A draft from an earlier visit is available on this device. Resume it or discard it before starting another draft.</Typography>
    {recovery.error && <Typography>{recovery.error}</Typography>}
    <Stack direction="row" spacing={1}><Button disabled={!recovery.ready} onClick={recovery.resume}>Resume draft</Button><Button disabled={recovery.resuming} onClick={recovery.discard}>Discard draft</Button></Stack>
  </Alert>
  if (recovery.error) return <Alert severity="warning">{recovery.error}<Button onClick={recovery.discard}>Discard recovery draft</Button></Alert>
  return recovery.kept ? <Typography variant="body2" aria-live="polite" color="text.secondary">Draft kept on this device · Use Save to save it to your account.</Typography> : null
}
