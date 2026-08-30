import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

import { compactEvidence, redactSecrets } from './redaction.js'
import type { CaseState, SmokeContext } from './smoke-context.js'

export async function ensureRunDirectories(config: SmokeContext['config']): Promise<void> {
  await Promise.all([
    mkdir(config.runDir, { recursive: true }),
    mkdir(path.join(config.runDir, 'screenshots'), { recursive: true }),
    mkdir(path.join(config.runDir, 'api-evidence'), { recursive: true }),
    mkdir(path.join(config.runDir, 'inputs'), { recursive: true }),
    mkdir(path.join(config.runDir, 'midscene-reports'), { recursive: true }),
  ])
}

export async function writeCaseEvidence(context: SmokeContext, state: CaseState): Promise<string> {
  await ensureRunDirectories(context.config)
  const evidencePath = path.join(context.config.runDir, 'api-evidence', `${state.name}.json`)
  const payload = redactSecrets({
    schema_version: 1,
    run_id: context.config.runId,
    case: state.name,
    started_at: state.startedAt,
    captured_at: new Date().toISOString(),
    api: state.apiEvidence,
    browser_api: state.browserEvidence,
    streams: state.streamCaptures.map((capture) => ({
      path: capture.path,
      request: capture.request,
      status: capture.status,
      event_types: capture.events.map((event) => event.type ?? 'unknown'),
      trace_ids: [...new Set(capture.events.flatMap((event) => {
        const candidates = [event.trace_id, (event.data as Record<string, unknown> | undefined)?.trace_id]
        return candidates.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
      }))],
      events: capture.events.map((event) => compactEvidence(event, context.config.evidencePreviewChars)),
    })),
    resources: state.resources,
    cleanup: state.cleanup,
  })
  await writeFile(evidencePath, `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600 })
  return evidencePath
}
