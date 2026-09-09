import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Box, Button, CircularProgress, Collapse, IconButton, Stack, Typography } from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import type { SSEEvent } from '@/hooks/useChatStream'
import { getStreamEventSessionId } from '@/lib/streamEventSession'
import FlowRunCompletionCard, { type FlowRunCompletionSummary } from './FlowRunCompletionCard'
import FlowShortcutCards from './FlowShortcutCards'
import { subscribeToFlowListInvalidation } from '@/features/flows/flowListInvalidation'
import { listFlows, type FlowSummaryResponse } from '@/services/agentStudioService'
import { getFlowShortcuts, saveFlowShortcuts, type FlowShortcuts } from '@/services/flowShortcutService'
import logger from '@/services/logger'

export interface CurationFlowsProps {
  /** Current chat session ID */
  sessionId: string | null
  /** Shared SSE event stream for reacting to flow completion */
  sseEvents: SSEEvent[]
  /** Callback to execute a flow */
  onExecuteFlow: (flowId: string, documentId?: string, userQuery?: string) => Promise<void>
  /** Callback to stop currently executing flow/chat stream */
  onStopFlow?: () => void | Promise<void>
  /** Whether a flow is currently executing */
  isExecuting?: boolean
  /** Current document loaded in PDF viewer */
  currentDocumentId?: string
}

export function mapFlowFinishedEvent(
  event: SSEEvent,
  sessionId: string | null,
): FlowRunCompletionSummary | null {
  if (event.type !== 'FLOW_FINISHED') {
    return null
  }

  const eventSessionId = getStreamEventSessionId(event)
  if (sessionId && eventSessionId && eventSessionId !== sessionId) {
    return null
  }

  const flowRunId = typeof event.flow_run_id === 'string' ? event.flow_run_id.trim() : ''
  const flowName = typeof event.flow_name === 'string' ? event.flow_name.trim() : ''
  const status = typeof event.status === 'string' ? event.status.trim() : ''
  const totalEvidenceRecords = Number(event.total_evidence_records)
  if (!flowRunId || !flowName || !status || !Number.isFinite(totalEvidenceRecords)) {
    return null
  }

  return {
    adapterKeys: Array.isArray(event.adapter_keys)
      ? event.adapter_keys.filter((value): value is string => typeof value === 'string' && value.length > 0)
      : [],
    documentId: typeof event.document_id === 'string' && event.document_id.length > 0
      ? event.document_id
      : null,
    extractionResultIds: Array.isArray(event.extraction_result_ids)
      ? event.extraction_result_ids.filter((value): value is string => typeof value === 'string' && value.length > 0)
      : [],
    extractionResultRefs: Array.isArray(event.extraction_result_refs)
      ? event.extraction_result_refs.filter(
        (value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value),
      )
      : [],
    flowId: typeof event.flow_id === 'string' ? event.flow_id : null,
    flowName,
    flowRunId,
    originSessionId: typeof event.origin_session_id === 'string' && event.origin_session_id.length > 0
      ? event.origin_session_id
      : null,
    reviewSessionIds: Array.isArray(event.review_session_ids)
      ? event.review_session_ids.filter((value): value is string => typeof value === 'string')
      : [],
    status,
    failureReason: typeof event.failure_reason === 'string' && event.failure_reason.length > 0
      ? event.failure_reason
      : null,
    totalEvidenceRecords,
  }
}


export default function CurationFlows({ sessionId, sseEvents, onExecuteFlow, onStopFlow, isExecuting = false, currentDocumentId }: CurationFlowsProps) {
  const navigate = useNavigate()
  const [flows, setFlows] = useState<FlowSummaryResponse[]>([])
  const [preferences, setPreferences] = useState<FlowShortcuts>({ flow_ids: null, revision: 0 })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const busy = useRef(false)
  const requestId = useRef(0)
  const [error, setError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [runningId, setRunningId] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const selectedIds = preferences.flow_ids === null ? flows.map(flow => flow.id) : preferences.flow_ids.filter(id => flows.some(flow => flow.id === id))
  const latestCompletedRun = useMemo(() => {
    for (let i = sseEvents.length - 1; i >= 0; i--) {
      const summary = mapFlowFinishedEvent(sseEvents[i], sessionId)
      if (summary) return summary
    }
    return null
  }, [sseEvents, sessionId])
  const fetchFlows = useCallback(async () => {
    if (busy.current) return
    const id = ++requestId.current
    setLoading(true); setError(null)
    try {
      const [first, saved] = await Promise.all([listFlows(), getFlowShortcuts()])
      const all = [...first.flows]
      for (let page = 2; all.length < first.total; page++) {
        const next = await listFlows(page, first.page_size)
        if (!next.flows.length) break
        all.push(...next.flows)
      }
      if (id !== requestId.current) return
      setFlows([...new Map(all.map(flow => [flow.id, flow])).values()]); setPreferences(saved); setSaveError(null)
    } catch (err) {
      if (id !== requestId.current) return
      logger.error('Failed to load flow shortcuts', err as Error, { component: 'CurationFlows' })
      setError(err instanceof Error ? err.message : 'Your flow list could not be loaded.')
    } finally { if (id === requestId.current) setLoading(false) }
  }, [])
  useEffect(() => { void fetchFlows(); return () => { requestId.current++ } }, [fetchFlows])
  useEffect(() => subscribeToFlowListInvalidation(() => { void fetchFlows() }), [fetchFlows])
  const change = async (ids: string[]) => {
    if (busy.current) return false
    busy.current = true; setSaving(true); setSaveError(null)
    try { setPreferences(await saveFlowShortcuts(ids, preferences.revision)); return true }
    catch (err) { setSaveError(err instanceof Error ? err.message : 'Your list could not be saved.'); return false }
    finally { busy.current = false; setSaving(false) }
  }
  const run = async (flow: FlowSummaryResponse) => {
    if (!sessionId || isExecuting) return
    setRunningId(flow.id)
    try { await onExecuteFlow(flow.id, currentDocumentId) }
    catch (err) { logger.error('Flow execution failed', err as Error, { component: 'CurationFlows' }) }
    finally { setRunningId(null) }
  }
  return <Box sx={{ border: 1, borderColor: 'divider', borderRadius: 2, mb: 2, overflow: 'hidden' }}>
    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 2, py: 1 }}>
      <Typography variant="subtitle2" fontWeight={600}>Curation Flows</Typography>
      <IconButton aria-expanded={!collapsed} aria-label={collapsed ? 'Expand curation flows' : 'Collapse curation flows'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <ExpandMoreIcon /> : <ExpandLessIcon />}</IconButton>
    </Stack>
    <Collapse in={!collapsed}><Stack spacing={1.5} sx={{ p: 2, pt: 0 }}>
      {latestCompletedRun && <FlowRunCompletionCard run={latestCompletedRun} />}
      {loading && <Box sx={{ textAlign: 'center', p: 2 }}><CircularProgress size={24} aria-label="Loading flows" /></Box>}
      {error && <Alert severity="error" action={<Button onClick={() => void fetchFlows()}>Retry</Button>}>{error}</Alert>}
      {saveError && <Alert severity="error" action={<Button disabled={saving} onClick={() => void fetchFlows()}>Refresh</Button>}>{saveError}</Alert>}
      {!loading && !error && <FlowShortcutCards flows={flows} selectedIds={selectedIds} saving={saving} onChange={change} onRun={flow => void run(flow)} onStop={onStopFlow ? () => { void onStopFlow() } : undefined} runningId={runningId} canRun={Boolean(sessionId) && !isExecuting} onOpenWorkspace={() => navigate('/agent-studio?tab=flows')} />}
    </Stack></Collapse>
  </Box>
}
