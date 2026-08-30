import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  fatalSseErrors,
  flowFinishedEventSchema,
  flowGroundingForRun,
  fileReadyCleanupIdentities,
  groundingEvidenceForDocument,
  parseSseEvents,
} from '../src/sse.js'

const documentId = '7350777a-cee9-483a-ac79-2c68093d8287'
const otherDocumentId = 'e1ea649b-edab-4df4-bc2f-9a1f4a3ed512'
const flowId = '0cbcf078-62cf-414f-970a-e049a32b9dd2'
const flowRunId = 'd88f1c05-3feb-481d-aa4d-7ab3ff46fc6e'
const evidence = {
  evidence_record_id: 'evidence-1', entity: 'crb/Crumbs',
  verified_quote: 'The crb mutant changes Crumbs protein abundance.',
  document_id: documentId, chunk_id: 'chunk-1', page: 7,
}

describe('SSE evidence parsing', () => {
  it('parses data frames and ignores keepalives and DONE', () => {
    assert.deepEqual(parseSseEvents(': ping\ndata: {"type":"RUN_STARTED","trace_id":"trace-1"}\n\ndata: [DONE]\n'), [
      { type: 'RUN_STARTED', trace_id: 'trace-1' },
    ])
  })

  it('accepts chat grounding only for the expected document, session, trace, and entity', () => {
    const event = {
      type: 'evidence_summary',
      session_id: 'session-1', trace_id: 'trace-1', evidence_records: [evidence],
    }
    assert.equal(groundingEvidenceForDocument([event], {
      documentId, sessionId: 'session-1', traceId: 'trace-1', entity: /crb|crumbs/i,
    }).length, 1)
    assert.equal(groundingEvidenceForDocument([event], {
      documentId: otherDocumentId, sessionId: 'session-1', traceId: 'trace-1', entity: /crb|crumbs/i,
    }).length, 0)
    assert.equal(groundingEvidenceForDocument([event], {
      documentId, sessionId: 'other-session', traceId: 'trace-1', entity: /crb|crumbs/i,
    }).length, 0)
  })

  it('rejects flow evidence from another run, document, or agent and failed terminal status', () => {
    const event = {
      type: 'FLOW_STEP_EVIDENCE', flow_id: flowId, flow_run_id: flowRunId,
      agent_id: 'gene_extractor', evidence_count: 1, evidence_records: [evidence],
    }
    const expected = {
      flowId, flowRunId, documentId, agentId: 'gene_extractor', entity: /crb|crumbs/i,
    }
    assert.equal(flowGroundingForRun([event], expected).length, 1)
    assert.equal(flowGroundingForRun([event], { ...expected, documentId: otherDocumentId }).length, 0)
    assert.equal(flowGroundingForRun([event], { ...expected, flowRunId: otherDocumentId }).length, 0)
    assert.equal(flowGroundingForRun([event], { ...expected, agentId: 'other_agent' }).length, 0)
    assert.equal(flowFinishedEventSchema.safeParse({
      type: 'FLOW_FINISHED', status: 'failed', flow_id: flowId, flow_run_id: flowRunId,
      document_id: documentId, total_evidence_records: 1,
    }).success, false)
  })

  it('tracks minimal FILE_READY identity even when semantic metadata regresses', () => {
    assert.deepEqual(fileReadyCleanupIdentities([{
      type: 'FILE_READY',
      details: { file_id: flowRunId, filename: 'agent-smoke-run-1-output.json', flow_id: 'malformed' },
    }]), [{ id: flowRunId, filename: 'agent-smoke-run-1-output.json' }])
  })

  it('keeps the documented nonfatal validator warning separate from fatal errors', () => {
    assert.deepEqual(fatalSseErrors([{
      type: 'SPECIALIST_ERROR',
      details: { reason: 'domain_validator_dispatch_error', fatal: false, severity: 'warning' },
    }]), [])
    assert.equal(fatalSseErrors([{ type: 'FLOW_ERROR', message: 'failed' }]).length, 1)
  })
})
