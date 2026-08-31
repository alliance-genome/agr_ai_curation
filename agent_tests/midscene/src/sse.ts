import { z } from 'zod'

import { compactDiagnostic } from './redaction.js'

export type SseEvent = Record<string, unknown> & { type?: string }

export const groundingEvidenceRecordSchema = z.object({
  evidence_record_id: z.string().min(1),
  entity: z.string().min(1),
  verified_quote: z.string().min(1),
  document_id: z.string().min(1),
  chunk_id: z.string().min(1),
  page: z.union([z.number().int().positive(), z.string().min(1)]).optional(),
  section: z.string().min(1).optional(),
}).passthrough().refine(
  (record) => record.page !== undefined || record.section !== undefined,
  'grounding evidence requires a page or section',
)

export const evidenceSummaryEventSchema = z.object({
  type: z.literal('evidence_summary'),
  session_id: z.string().min(1),
  trace_id: z.string().min(1),
  evidence_records: z.array(groundingEvidenceRecordSchema).min(1),
}).passthrough()

export const flowStepEvidenceEventSchema = z.object({
  type: z.literal('FLOW_STEP_EVIDENCE'),
  flow_id: z.string().uuid(),
  flow_run_id: z.string().uuid(),
  agent_id: z.string().min(1),
  evidence_count: z.coerce.number().int().nonnegative(),
  evidence_records: z.array(groundingEvidenceRecordSchema),
}).passthrough()

export const fileReadyEventSchema = z.object({
  type: z.literal('FILE_READY'),
  details: z.object({
    file_id: z.string().uuid(),
    filename: z.string().min(1).max(512),
    format: z.enum(['json', 'csv', 'tsv']),
    size_bytes: z.number().int().positive(),
    download_url: z.string().regex(/^\/api\/files\/[0-9a-f-]+\/download$/i),
    flow_id: z.string().uuid(),
    flow_run_id: z.string().uuid(),
    formatter_node_id: z.string().min(1),
    source_node_id: z.string().min(1),
    document_id: z.string().uuid(),
  }).passthrough(),
}).passthrough()

export const fileReadyCleanupIdentitySchema = z.object({
  type: z.literal('FILE_READY'),
  details: z.object({
    file_id: z.string().uuid(),
    filename: z.string().min(1).max(512),
  }).passthrough(),
}).passthrough()

export const flowFinishedEventSchema = z.object({
  type: z.literal('FLOW_FINISHED'),
  status: z.literal('completed'),
  flow_id: z.string().uuid(),
  flow_run_id: z.string().uuid(),
  document_id: z.string().uuid(),
  total_evidence_records: z.coerce.number().int().positive(),
}).passthrough()

export const flowEvidenceExportSchema = z.object({
  flow_name: z.string().min(1),
  flow_run_id: z.string().uuid(),
  total_evidence_records: z.coerce.number().int().positive(),
  steps: z.array(z.object({
    agent_id: z.string().min(1),
    evidence_count: z.coerce.number().int().nonnegative(),
    evidence_records: z.array(groundingEvidenceRecordSchema),
  }).passthrough()).min(1),
}).passthrough()

export type FileReadyEvent = z.infer<typeof fileReadyEventSchema>
export type GroundingEvidenceRecord = z.infer<typeof groundingEvidenceRecordSchema>

export function parseSseEvents(text: string, evidencePreviewChars: number): SseEvent[] {
  const events: SseEvent[] = []
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith('data:')) continue
    const payload = line.slice(5).trim()
    if (!payload || payload === '[DONE]') continue
    let parsed: unknown
    try { parsed = JSON.parse(payload) } catch (error) {
      throw new Error(`invalid SSE JSON payload: ${compactDiagnostic(payload, evidencePreviewChars)}`, { cause: error })
    }
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) events.push(parsed as SseEvent)
  }
  return events
}

export function eventField(event: SseEvent, key: string): unknown {
  if (key in event) return event[key]
  const data = event.data
  return data && typeof data === 'object' && !Array.isArray(data)
    ? (data as Record<string, unknown>)[key]
    : undefined
}

export function eventString(event: SseEvent, key: string): string {
  const value = eventField(event, key)
  return typeof value === 'string' ? value.trim() : ''
}

export function groundingEvidenceForDocument(
  events: readonly SseEvent[],
  expected: { documentId: string; sessionId: string; traceId: string; entity: RegExp },
): GroundingEvidenceRecord[] {
  return events.flatMap((event) => {
    const parsed = evidenceSummaryEventSchema.safeParse(event)
    if (!parsed.success
      || parsed.data.session_id !== expected.sessionId
      || parsed.data.trace_id !== expected.traceId) return []
    return parsed.data.evidence_records.filter((record) => record.document_id === expected.documentId
      && expected.entity.test(record.entity)
      && expected.entity.test(record.verified_quote))
  })
}

export function fileReadyEvents(events: readonly SseEvent[]): FileReadyEvent[] {
  return events.flatMap((event) => {
    const parsed = fileReadyEventSchema.safeParse(event)
    return parsed.success ? [parsed.data] : []
  })
}

export function fileReadyCleanupIdentities(events: readonly SseEvent[]): Array<{ id: string; filename: string }> {
  return events.flatMap((event) => {
    const parsed = fileReadyCleanupIdentitySchema.safeParse(event)
    return parsed.success ? [{ id: parsed.data.details.file_id, filename: parsed.data.details.filename }] : []
  })
}

export function flowGroundingForRun(
  events: readonly SseEvent[],
  expected: { flowId: string; flowRunId: string; documentId: string; agentId: string; entity: RegExp },
): GroundingEvidenceRecord[] {
  return events.flatMap((event) => {
    const parsed = flowStepEvidenceEventSchema.safeParse(event)
    if (!parsed.success
      || parsed.data.flow_id !== expected.flowId
      || parsed.data.flow_run_id !== expected.flowRunId
      || parsed.data.agent_id !== expected.agentId) return []
    return parsed.data.evidence_records.filter((record) => record.document_id === expected.documentId
      && expected.entity.test(record.entity)
      && expected.entity.test(record.verified_quote))
  })
}

export function fatalSseErrors(events: readonly SseEvent[]): SseEvent[] {
  return events.filter((event) => {
    const type = stringValue(event.type).toUpperCase()
    if (!type.endsWith('_ERROR')) return false
    if (type !== 'SPECIALIST_ERROR') return true
    const details = recordValue(event.details)
    return !(details?.reason === 'domain_validator_dispatch_error' && details.fatal === false && details.severity === 'warning')
  })
}

export function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}

export function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}
