import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

import { defineNode, z, type NodeDefinition } from '@midscene/test'

import { ApiClient, type ApiEvidence } from './api-client.js'
import { writeCaseEvidence } from './evidence.js'
import { assertExactSmokeGraph, buildSmokeFlowDefinition } from './graph.js'
import { removeLocalFileOutput } from './local-file-cleanup.js'
import { pollUntil } from './poll.js'
import { cleanupResources, emptyResources } from './resources.js'
import { bindAgentProviderContext } from './setup.js'
import type { CaseState, SmokeContext, StreamCapture } from './smoke-context.js'
import {
  eventString,
  fatalSseErrors,
  fileReadyEvents,
  flowEvidenceExportSchema,
  flowFinishedEventSchema,
  flowGroundingForRun,
  groundingEvidenceForDocument,
  recordValue,
  stringValue,
} from './sse.js'

const emptyInput = z.object({}).strict()
const beginInput = z.object({ caseName: z.string().min(1).max(80) }).strict()
const seedFlowInput = z.object({
  name: z.string().min(1).max(255),
  instructions: z.string().min(1).max(5_000),
  formatter: z.enum(['json_formatter', 'csv_formatter']),
}).strict()
const assertGraphInput = seedFlowInput.pick({ instructions: true, formatter: true })
const pathInput = z.object({ path: z.string().min(1) }).strict()

interface FlowResponse {
  id: string
  name: string
  flow_definition: unknown
  execution_count: number
  last_executed_at: string | null
}

interface DocumentResponse {
  document_id: string
  filename: string
  status: string
}

interface DocumentStatusResponse {
  document_id: string
  processing_status: string
  embedding_status?: string
  pipeline_status?: unknown
}

interface ChatHistoryDetail {
  session: { session_id: string; active_document_id?: string | null }
  active_document?: { id: string } | null
  messages: Array<{
    role: string
    message_type?: string
    content: string
    trace_id?: string | null
    payload_json?: unknown
  }>
}

function nestedString(value: unknown, expected: string): boolean {
  if (typeof value === 'string') return value === expected
  if (Array.isArray(value)) return value.some((item) => nestedString(item, expected))
  const record = recordValue(value)
  return record ? Object.values(record).some((item) => nestedString(item, expected)) : false
}

function current(context: SmokeContext): CaseState {
  if (!context.caseState) throw new Error('smoke.beginCase must run before this node')
  return context.caseState
}

function trackUnique(values: string[], value: string): void {
  if (!values.includes(value)) values.push(value)
}

function isCrbEvidence(record: { document_id: string; entity: string; verified_quote: string }, documentId: string): boolean {
  return record.document_id === documentId
    && /\bcrb\b|crumbs/i.test(record.entity)
    && /\bcrb\b|crumbs/i.test(record.verified_quote)
}

async function settleCaptures(context: SmokeContext): Promise<void> {
  const deadline = Date.now() + context.config.captureDrainTimeoutMs
  while (context.pendingCaptures.size > 0) {
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw new Error(`pending response capture drain exceeded ${context.config.captureDrainTimeoutMs}ms`)
    let timer: NodeJS.Timeout | undefined
    try {
      await Promise.race([
        Promise.allSettled([...context.pendingCaptures]),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error(`pending response capture drain exceeded ${context.config.captureDrainTimeoutMs}ms`)), remaining)
        }),
      ])
    } finally {
      if (timer) clearTimeout(timer)
    }
  }
}

function stateApi(context: SmokeContext, state: CaseState): ApiClient {
  const evidence: ApiEvidence[] = []
  state.apiEvidence = evidence
  return new ApiClient({
    baseUrl: context.config.appUrl,
    authMode: context.config.appAuth,
    secret: context.config.appSecret,
    timeoutMs: context.config.httpTimeoutMs,
    evidence,
    evidencePreviewChars: context.config.evidencePreviewChars,
  })
}

function abortSignal(context: SmokeContext, parent: AbortSignal): AbortSignal {
  return AbortSignal.any([parent, AbortSignal.timeout(context.config.pdfProcessingTimeoutMs)])
}

async function getFlow(context: SmokeContext): Promise<FlowResponse> {
  const state = current(context)
  if (!state.flowId) {
    await settleCaptures(context)
  }
  if (!state.flowId) throw new Error('no flow ID was captured or seeded for the case')
  return context.api.get<FlowResponse>(`/api/flows/${encodeURIComponent(state.flowId)}`)
}

async function uploadPreparedFile(context: SmokeContext): Promise<DocumentResponse> {
  const state = current(context)
  if (!state.preparedFile) throw new Error('no prepared PDF file is available')
  const bytes = await readFile(state.preparedFile)
  const pdfBytes = new Uint8Array(bytes.length)
  pdfBytes.set(bytes)
  const form = new FormData()
  form.append('file', new Blob([pdfBytes], { type: 'application/pdf' }), path.basename(state.preparedFile))
  const response = await context.api.postForm<DocumentResponse>(
    '/api/weaviate/documents/upload', form, { filename: path.basename(state.preparedFile), size_bytes: bytes.length },
  )
  state.documentId = response.document_id
  trackUnique(state.resources.documentIds, response.document_id)
  return response
}

async function prepareSaltedPdf(context: SmokeContext): Promise<string> {
  const state = current(context)
  const source = path.join(context.config.repoRoot, 'backend/tests/fixtures/sample_fly_publication.pdf')
  const destination = path.join(context.config.runDir, 'inputs', `${context.config.runPrefix}-sample-fly-publication.pdf`)
  const original = await readFile(source)
  const salt = Buffer.from(`\n% ${context.config.runPrefix} ${state.name}\n`, 'utf8')
  await writeFile(destination, Buffer.concat([original, salt]), { mode: 0o600 })
  state.preparedFile = destination
  return destination
}

function streamFor(state: CaseState, pathName: StreamCapture['path']): StreamCapture {
  const stream = [...state.streamCaptures].reverse().find((capture) => capture.path === pathName)
  if (!stream) throw new Error(`no completed ${pathName} response was captured`)
  if (stream.status !== 200) throw new Error(`${pathName} returned ${stream.status}`)
  const fatal = fatalSseErrors(stream.events)
  if (fatal.length > 0) throw new Error(`${pathName} emitted fatal errors: ${JSON.stringify(fatal)}`)
  return stream
}

const beginCaseNode = defineNode<typeof beginInput, unknown, SmokeContext>({
  name: 'smoke.beginCase',
  description: 'Initialize isolated evidence and resource tracking for one smoke case.',
  inputSchema: beginInput,
  async execute({ context, input, onTeardown }) {
    bindAgentProviderContext(context)
    if (context.caseState && context.caseState.resources.flowIds.length
      + context.caseState.resources.documentIds.length
      + context.caseState.resources.chatSessionIds.length
      + context.caseState.resources.fileOutputs.length > 0) {
      throw new Error('the previous smoke case left tracked resources behind')
    }
    context.caseState = {
      name: input.caseName,
      startedAt: new Date().toISOString(),
      resources: emptyResources(),
      apiEvidence: [],
      browserEvidence: [],
      streamCaptures: [],
    }
    context.api = stateApi(context, context.caseState)
    const state = context.caseState
    onTeardown(async () => {
      if (state.cleanup || context.config.retainResources) return
      let captureFailure: string | undefined
      try { await settleCaptures(context) } catch (error) {
        captureFailure = error instanceof Error ? error.message : String(error)
      }
      state.cleanup = await cleanupResources({
        api: context.api, resources: state.resources, retain: false,
        removeFileOutput: (file) => removeLocalFileOutput(context.config, file),
        retryCount: context.config.cleanupRetryCount,
        retryIntervalMs: context.config.cleanupRetryIntervalMs,
      })
      if (captureFailure) state.cleanup.failures.unshift(`pending-captures: ${captureFailure}`)
      await writeCaseEvidence(context, state)
      if (state.cleanup.failures.length > 0) throw new Error(`teardown cleanup failures: ${state.cleanup.failures.join('; ')}`)
    })
    return { summary: `Started ${input.caseName} with prefix ${context.config.runPrefix}` }
  },
})

const finishCaseNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'smoke.finishCase',
  description: 'Capture a screenshot and sanitized evidence, then clean every tracked resource.',
  inputSchema: emptyInput,
  async execute({ context }) {
    const state = current(context)
    await settleCaptures(context)
    const screenshotPath = path.join(context.config.runDir, 'screenshots', `${state.name}.png`)
    let screenshotError: unknown
    try { await context.page.screenshot({ path: screenshotPath, fullPage: true, timeout: context.config.stepTimeoutMs }) } catch (error) { screenshotError = error }
    state.cleanup = await cleanupResources({
      api: context.api,
      resources: state.resources,
      removeFileOutput: (file) => removeLocalFileOutput(context.config, file),
      retain: context.config.retainResources,
      retryCount: context.config.cleanupRetryCount,
      retryIntervalMs: context.config.cleanupRetryIntervalMs,
    })
    const evidencePath = await writeCaseEvidence(context, state)
    state.resources = emptyResources()
    const failures = [...state.cleanup.failures]
    if (screenshotError) failures.push(`screenshot: ${screenshotError instanceof Error ? screenshotError.message : String(screenshotError)}`)
    if (failures.length > 0) throw new Error(failures.join('; '))
    return { summary: `${state.name} evidence: ${evidencePath}` }
  },
})

const preparePdfNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'document.prepareSaltedPdf',
  description: 'Create a uniquely salted copy of the committed sample publication.',
  inputSchema: emptyInput,
  async execute({ context }) {
    const file = await prepareSaltedPdf(context)
    return { summary: `Prepared ${path.basename(file)}`, data: { filename: path.basename(file) } }
  },
})

const selectPreparedFileNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'document.selectPreparedFile',
  description: 'Select the prepared PDF through the real hidden browser file input.',
  inputSchema: emptyInput,
  async execute({ context }) {
    const state = current(context)
    if (!state.preparedFile) throw new Error('document.prepareSaltedPdf must run first')
    const responsePromise = context.page.waitForResponse(
      (response) => response.url().includes('/api/weaviate/documents/upload') && response.request().method() === 'POST',
      { timeout: context.config.uploadTimeoutMs },
    )
    await context.page.locator('input[type="file"][accept*="application/pdf"]').setInputFiles(state.preparedFile, {
      timeout: context.config.stepTimeoutMs,
    })
    const response = await responsePromise
    const body = await response.json() as DocumentResponse
    if (!response.ok()) throw new Error(`UI document upload returned ${response.status()}`)
    assert.equal(body.filename, path.basename(state.preparedFile))
    state.documentId = body.document_id
    trackUnique(state.resources.documentIds, body.document_id)
    return { summary: `Uploaded ${body.filename} as ${body.document_id}` }
  },
})

const uploadPreparedFileNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'document.uploadPreparedFile',
  description: 'Upload the salted PDF through the authenticated API for flow-run setup.',
  inputSchema: emptyInput,
  async execute({ context }) {
    const response = await uploadPreparedFile(context)
    return { summary: `Uploaded ${response.filename} as ${response.document_id}` }
  },
})

const awaitDocumentNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'document.awaitProcessing',
  description: 'Poll the durable document status until processing completes.',
  inputSchema: emptyInput,
  async execute(ctx) {
    const state = current(ctx.context)
    if (!state.documentId) throw new Error('no document ID is available')
    const status = await pollUntil(
      () => ctx.context.api.get<DocumentStatusResponse>(`/api/weaviate/documents/${encodeURIComponent(state.documentId!)}/status`),
      (value) => value.processing_status.toLowerCase() === 'completed',
      {
        label: `document ${state.documentId} processing`,
        intervalMs: ctx.context.config.pdfPollIntervalMs,
        limit: ctx.context.config.pdfPollLimit,
        evidencePreviewChars: ctx.context.config.evidencePreviewChars,
        signal: abortSignal(ctx.context, ctx.signal),
      },
    )
    return { summary: `Document ${status.document_id} processing completed` }
  },
})

const loadDocumentNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'document.loadViaApi',
  description: 'Load the prepared document deterministically for the flow-run setup.',
  inputSchema: emptyInput,
  async execute({ context }) {
    const state = current(context)
    if (!state.documentId) throw new Error('no document ID is available')
    await context.api.post('/api/chat/document/load', { document_id: state.documentId })
    state.resources.loadedDocument = true
    return { summary: `Loaded document ${state.documentId} for chat` }
  },
})

const assertActiveDocumentNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'document.assertActive',
  description: 'Assert the durable active-document state after the curator UI load action.',
  inputSchema: emptyInput,
  async execute({ context }) {
    const state = current(context)
    if (!state.documentId) throw new Error('no document ID is available')
    const response = await context.api.get<{ active: boolean; document?: { id: string } }>('/api/chat/document')
    assert.equal(response.active, true)
    assert.equal(response.document?.id, state.documentId)
    state.resources.loadedDocument = true
    return { summary: `Active document is ${state.documentId}` }
  },
})

const seedFlowNode = defineNode<typeof seedFlowInput, unknown, SmokeContext>({
  name: 'flow.seed',
  description: 'Create a known flow through the API before the curator edits or runs it.',
  inputSchema: seedFlowInput,
  async execute({ context, input }) {
    const response = await context.api.post<FlowResponse>('/api/flows', {
      name: input.name,
      description: `Local Midscene smoke resource ${context.config.runPrefix}`,
      flow_definition: buildSmokeFlowDefinition({ instructions: input.instructions, formatter: input.formatter }),
    })
    const state = current(context)
    state.flowId = response.id
    state.flowExecutionCount = response.execution_count
    trackUnique(state.resources.flowIds, response.id)
    return { summary: `Seeded flow ${response.name} (${response.id})` }
  },
})

const assertGraphNode = defineNode<typeof assertGraphInput, unknown, SmokeContext>({
  name: 'flow.assertGraph',
  description: 'Assert exact persisted nodes, entry node, instructions, and edge roles.',
  inputSchema: assertGraphInput,
  async execute({ context, input }) {
    await settleCaptures(context)
    const flow = await pollUntil(
      () => getFlow(context),
      (value) => {
        try {
          assertExactSmokeGraph(value.flow_definition, { taskInstructions: input.instructions, formatter: input.formatter })
          return true
        } catch { return false }
      },
      {
        label: 'persisted smoke graph',
        intervalMs: context.config.persistencePollIntervalMs,
        limit: context.config.persistencePollLimit,
        evidencePreviewChars: context.config.evidencePreviewChars,
      },
    )
    assertExactSmokeGraph(flow.flow_definition, { taskInstructions: input.instructions, formatter: input.formatter })
    return { summary: `Persisted flow ${flow.id} has the exact intended graph` }
  },
})

const chatAssertInput = z.object({ question: z.string().min(1).max(2_000) }).strict()

const assertChatNode = defineNode<typeof chatAssertInput, unknown, SmokeContext>({
  name: 'chat.assertDurableGroundedAnswer',
  description: 'Assert the durable transcript, active document, trace ID, and grounding evidence.',
  inputSchema: chatAssertInput,
  async execute({ context, input }) {
    await settleCaptures(context)
    const state = current(context)
    const stream = streamFor(state, '/api/chat/stream')
    const sessionId = stringValue(stream.request.session_id) || state.chatSessionId
    if (!sessionId) throw new Error('chat stream request did not expose a session_id')
    state.chatSessionId = sessionId
    trackUnique(state.resources.chatSessionIds, sessionId)
    const terminal = stream.events.some((event) => event.type === 'RUN_FINISHED' || event.type === 'turn_completed')
    assert.equal(terminal, true, 'chat stream lacks a terminal success event')
    const runStarted = stream.events.find((event) => event.type === 'RUN_STARTED')
    const traceId = runStarted ? eventString(runStarted, 'trace_id') : ''
    assert.ok(traceId, 'RUN_STARTED lacks trace_id')
    assert.ok(state.documentId, 'chat case has no expected document ID')
    const grounding = groundingEvidenceForDocument(stream.events, {
      documentId: state.documentId,
      sessionId,
      traceId,
      entity: /\bcrb\b|crumbs/i,
    })
    assert.ok(grounding.length > 0, 'chat stream lacks crb grounding linked to the expected document, session, and trace')

    const detail = await pollUntil(
      () => context.api.get<ChatHistoryDetail>(`/api/chat/history/${encodeURIComponent(sessionId)}?message_limit=100`),
      (value) => value.messages.some((message) => message.role === 'assistant' && /\bcrb\b|crumbs/i.test(message.content)),
      {
        label: `durable chat session ${sessionId}`,
        intervalMs: context.config.persistencePollIntervalMs,
        limit: context.config.persistencePollLimit,
        evidencePreviewChars: context.config.evidencePreviewChars,
      },
    )
    assert.equal(detail.session.active_document_id, state.documentId)
    assert.equal(detail.active_document?.id, state.documentId)
    assert.ok(detail.messages.some((message) => message.role === 'user' && message.content.trim() === input.question))
    const answer = detail.messages.find((message) => message.role === 'assistant' && /\bcrb\b|crumbs/i.test(message.content))
    assert.ok(answer?.trace_id)
    assert.equal(answer.trace_id, traceId)
    return { summary: `Durable grounded answer is linked to trace ${traceId}` }
  },
})

const assertFlowRunNode = defineNode<typeof emptyInput, unknown, SmokeContext>({
  name: 'flow.assertDurableRun',
  description: 'Assert flow execution count, run identifiers, step evidence, and JSON evidence export.',
  inputSchema: emptyInput,
  async execute({ context }) {
    await settleCaptures(context)
    const state = current(context)
    const stream = streamFor(state, '/api/chat/execute-flow')
    const flowRunId = stream.events.map((event) => eventString(event, 'flow_run_id')).find(Boolean)
    assert.ok(flowRunId, 'flow stream lacks a real flow_run_id')
    const runStarted = stream.events.find((event) => event.type === 'RUN_STARTED')
    const traceId = runStarted ? eventString(runStarted, 'trace_id') : ''
    assert.ok(traceId, 'flow RUN_STARTED lacks trace_id')
    assert.ok(state.flowId, 'flow case has no expected flow ID')
    assert.ok(state.documentId, 'flow case has no expected document ID')

    const finished = flowFinishedEventSchema.parse(stream.events.find((event) => event.type === 'FLOW_FINISHED'))
    assert.equal(finished.flow_id, state.flowId)
    assert.equal(finished.flow_run_id, flowRunId)
    assert.equal(finished.document_id, state.documentId)

    const geneEvidence = flowGroundingForRun(stream.events, {
      flowId: state.flowId,
      flowRunId,
      documentId: state.documentId,
      agentId: 'gene_extractor',
      entity: /\bcrb\b|crumbs/i,
    })
    assert.ok(geneEvidence.length > 0, 'flow stream lacks crb evidence from gene_extractor linked to the expected document and run')

    const outputEvents = fileReadyEvents(stream.events)
    assert.ok(outputEvents.length > 0, 'flow stream lacks a validated FILE_READY output')
    const exportDir = path.join(context.config.runDir, 'file-exports')
    await mkdir(exportDir, { recursive: true })
    for (const output of outputEvents) {
      const details = output.details
      assert.equal(details.flow_id, state.flowId)
      assert.equal(details.flow_run_id, flowRunId)
      assert.equal(details.document_id, state.documentId)
      assert.equal(details.format, 'json')
      assert.ok(details.filename.startsWith(`${context.config.runPrefix}-`), 'flow output filename lacks the exact smoke run prefix boundary')
      assert.ok(nestedString(finished, details.file_id), 'FLOW_FINISHED is not linked to the FILE_READY output')
      if (!state.resources.fileOutputs.some((file) => file.id === details.file_id)) {
        state.resources.fileOutputs.push({ id: details.file_id, filename: details.filename })
      }
      const bytes = await context.api.download(details.download_url)
      assert.equal(bytes.length, details.size_bytes)
      const outputJson: unknown = JSON.parse(new TextDecoder().decode(bytes))
      assert.ok(JSON.stringify(outputJson).length > 2, 'downloaded JSON file output is empty')
      await writeFile(path.join(exportDir, path.basename(details.filename)), bytes, { mode: 0o600 })
    }

    const flow = await pollUntil(
      () => getFlow(context),
      (value) => value.execution_count > (state.flowExecutionCount ?? 0) && Boolean(value.last_executed_at),
      {
        label: `flow ${state.flowId} execution count`,
        intervalMs: context.config.persistencePollIntervalMs,
        limit: context.config.persistencePollLimit,
        evidencePreviewChars: context.config.evidencePreviewChars,
      },
    )
    assert.equal(flow.execution_count, (state.flowExecutionCount ?? 0) + 1)
    assert.ok(eventString(runStarted!, 'agent').includes(flow.name), 'flow RUN_STARTED is not linked to the expected flow')

    const sessionId = stringValue(stream.request.session_id) || state.chatSessionId
    assert.ok(sessionId, 'flow request lacks a durable session_id')
    state.chatSessionId = sessionId
    trackUnique(state.resources.chatSessionIds, sessionId)
    const history = await pollUntil(
      () => context.api.get<ChatHistoryDetail>(`/api/chat/history/${encodeURIComponent(sessionId)}?message_limit=100`),
      (value) => value.messages.some((message) => message.message_type === 'flow_step_evidence'),
      {
        label: `durable flow transcript ${sessionId}`,
        intervalMs: context.config.persistencePollIntervalMs,
        limit: context.config.persistencePollLimit,
        evidencePreviewChars: context.config.evidencePreviewChars,
      },
    )
    assert.equal(history.session.active_document_id, state.documentId)
    assert.equal(history.active_document?.id, state.documentId)
    assert.ok(
      history.messages.some((message) => message.message_type === 'flow_step_evidence'
        && nestedString(message.payload_json, 'FLOW_STEP_EVIDENCE')
        && nestedString(message.payload_json, flowRunId)),
      'durable flow transcript lacks step evidence linked to the flow run',
    )
    assert.ok(
      history.messages.some((message) => message.trace_id === traceId
        && nestedString(message.payload_json, flowRunId)),
      'durable flow transcript lacks matching trace_id and flow_run_id',
    )

    const exported = flowEvidenceExportSchema.parse(
      await context.api.get<unknown>(`/api/flows/runs/${encodeURIComponent(flowRunId)}/evidence/export?format=json`),
    )
    assert.equal(exported.flow_run_id, flowRunId)
    assert.equal(exported.flow_name, flow.name)
    assert.ok(exported.steps.some((step) => step.agent_id === 'gene_extractor'
      && step.evidence_records.some((record) => isCrbEvidence(record, state.documentId!))),
    'JSON evidence export lacks gene_extractor crb evidence for the expected document')
    return { summary: `Flow run ${flowRunId} exported ${exported.total_evidence_records} evidence record(s)` }
  },
})

const gotoPathNode = defineNode<typeof pathInput, unknown, SmokeContext>({
  name: 'smoke.gotoPath',
  description: 'Navigate to an application path using the configured local URL.',
  inputSchema: pathInput,
  async execute({ context, input }) {
    const url = new URL(input.path, `${context.config.appUrl}/`).toString()
    const response = await context.page.goto(url, { waitUntil: 'domcontentloaded', timeout: context.config.httpTimeoutMs })
    if (response && !response.ok()) throw new Error(`navigation to ${input.path} returned ${response.status()}`)
    return { summary: `Navigated to ${url}` }
  },
})

export const smokeNodes: readonly NodeDefinition<any, any, SmokeContext>[] = [
  beginCaseNode,
  finishCaseNode,
  gotoPathNode,
  preparePdfNode,
  selectPreparedFileNode,
  uploadPreparedFileNode,
  awaitDocumentNode,
  loadDocumentNode,
  assertActiveDocumentNode,
  seedFlowNode,
  assertGraphNode,
  assertChatNode,
  assertFlowRunNode,
]
