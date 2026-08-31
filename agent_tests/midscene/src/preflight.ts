import { execFile, spawn } from 'node:child_process'
import { once } from 'node:events'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { promisify } from 'node:util'
import readline from 'node:readline'

import { chromium } from 'playwright'

import { ApiClient } from './api-client.js'
import { applyProviderEnvironment, loadConfig } from './config.js'
import { pollUntil } from './poll.js'
import { redactText } from './redaction.js'
import { recordValue, stringValue } from './sse.js'

const execFileAsync = promisify(execFile)

interface RpcMessage {
  id?: number
  method?: string
  result?: unknown
  error?: { message?: string }
}

export async function openAiModelPreflight(
  options: { baseUrl: string; model: string; apiKey: string; timeoutMs: number },
  fetchImpl: typeof fetch = fetch,
): Promise<{ id: string }> {
  const modelUrl = new URL(`models/${encodeURIComponent(options.model)}`, `${options.baseUrl.replace(/\/$/, '')}/`)
  const response = await fetchImpl(modelUrl, {
    method: 'GET',
    headers: { Authorization: `Bearer ${options.apiKey}` },
    signal: AbortSignal.timeout(options.timeoutMs),
  })
  if (!response.ok) {
    throw new Error(`OpenAI model preflight failed for ${options.model}: HTTP ${response.status}`)
  }
  const payload = recordValue(await response.json())
  const id = stringValue(payload?.id)
  if (id !== options.model) {
    throw new Error(`OpenAI model preflight returned unexpected model id ${id || 'missing'}`)
  }
  return { id }
}

export function appendStderrPreview(current: string, chunk: string, evidencePreviewChars: number): string {
  return redactText(`${current}${chunk}`).slice(-evidencePreviewChars)
}

async function codexModels(timeoutMs: number, evidencePreviewChars: number): Promise<Array<Record<string, unknown>>> {
  const child = spawn('codex', ['app-server'], { stdio: ['pipe', 'pipe', 'pipe'] })
  if (!child.stdin || !child.stdout || !child.stderr) throw new Error('codex app-server stdio is unavailable')
  let stderr = ''
  child.stderr.on('data', (chunk) => { stderr = appendStderrPreview(stderr, String(chunk), evidencePreviewChars) })
  const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity })
  const queue: RpcMessage[] = []
  const waiters: Array<(message: RpcMessage) => void> = []
  lines.on('line', (line) => {
    let message: RpcMessage
    try { message = JSON.parse(line) as RpcMessage } catch { return }
    const waiter = waiters.shift()
    if (waiter) waiter(message)
    else queue.push(message)
  })
  const deadline = Date.now() + timeoutMs
  const nextMessage = async (): Promise<RpcMessage> => {
    const queued = queue.shift()
    if (queued) return queued
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw new Error('codex app-server preflight timed out')
    return Promise.race([
      new Promise<RpcMessage>((resolve) => waiters.push(resolve)),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error('codex app-server preflight timed out')), remaining)),
      once(child, 'exit').then(([code]) => { throw new Error(`codex app-server exited ${String(code)}: ${stderr.trim()}`) }),
    ])
  }
  let requestId = 0
  const send = (message: RpcMessage): void => { child.stdin!.write(`${JSON.stringify(message)}\n`) }
  const request = async (method: string, params: unknown): Promise<unknown> => {
    requestId += 1
    const id = requestId
    send({ id, method, result: undefined, ...{ params } } as RpcMessage & { params: unknown })
    for (;;) {
      const message = await nextMessage()
      if (message.id !== id) continue
      if (message.error) throw new Error(`codex app-server ${method} failed: ${message.error.message ?? 'unknown error'}`)
      return message.result
    }
  }
  try {
    await request('initialize', {
      clientInfo: { name: 'agr_ai_curation_midscene_preflight', title: 'AGR AI Curation Midscene Preflight', version: '0.1.0' },
      capabilities: { experimentalApi: false },
    })
    send({ method: 'initialized' })
    const result = recordValue(await request('model/list', { includeHidden: true, limit: 100 }))
    return Array.isArray(result?.data) ? result.data.map(recordValue).filter((value): value is Record<string, unknown> => Boolean(value)) : []
  } finally {
    lines.close()
    child.stdin.end()
    child.kill()
  }
}

export async function runPreflight(): Promise<Record<string, unknown>> {
  const config = loadConfig(process.env, { cwd: process.cwd(), requireSecrets: true })
  applyProviderEnvironment(config)
  const checks: Record<string, unknown> = {}
  const api = new ApiClient({
    baseUrl: config.appUrl,
    authMode: config.appAuth,
    secret: config.appSecret,
    timeoutMs: config.preflightTimeoutMs,
    evidencePreviewChars: config.evidencePreviewChars,
  })
  const user = await api.get<Record<string, unknown>>('/api/users/me')
  if (!user.user_id && !user.id) throw new Error('/api/users/me did not return an authenticated user')
  checks.app_authentication = { ok: true, mode: config.appAuth }

  const storageEvidenceDir = path.join(config.runDir, 'preflight')
  await execFileAsync(path.join(config.repoRoot, 'scripts/testing/file_output_storage_preflight.sh'), [], {
    cwd: config.repoRoot,
    timeout: config.preflightTimeoutMs,
    env: { ...process.env, EXPORT_STORAGE_PREFLIGHT_OUT_DIR: storageEvidenceDir },
  })
  checks.file_output_storage = { ok: true, evidence_dir: storageEvidenceDir }

  let health = await api.get<Record<string, unknown>>('/api/weaviate/documents/pdf-extraction-health')
  if (health.wake_required === true || health.worker_available !== true) {
    await api.post('/api/weaviate/documents/pdf-extraction-wake')
    health = await pollUntil(
      () => api.get<Record<string, unknown>>('/api/weaviate/documents/pdf-extraction-health'),
      (value) => value.status === 'healthy' && value.worker_available === true,
      {
        label: 'PDF extraction worker preflight',
        intervalMs: config.pdfPollIntervalMs,
        limit: config.pdfPollLimit,
        evidencePreviewChars: config.evidencePreviewChars,
        signal: AbortSignal.timeout(config.pdfProcessingTimeoutMs),
      },
    )
  }
  if (health.status !== 'healthy' || health.worker_available !== true) {
    throw new Error(`PDF processing preflight is not ready: status=${String(health.status)} worker=${String(health.worker_state)}`)
  }
  checks.pdf_processing = { ok: true, status: health.status, worker_state: health.worker_state }

  const executable = chromium.executablePath()
  if (!existsSync(executable)) throw new Error(`Playwright Chromium is not installed at ${executable}`)
  const browser = await chromium.launch({ headless: true, timeout: config.preflightTimeoutMs })
  await browser.close()
  checks.browser = { ok: true, executable }

  if (config.provider === 'codex') {
    let login
    try {
      login = await execFileAsync('codex', ['login', 'status'], { timeout: config.preflightTimeoutMs })
    } catch {
      throw new Error(
        'Codex login is unavailable for the runner OS account; use device auth or the protected credential-cache procedure in agent_tests/midscene/README.md',
      )
    }
    const loginStatus = `${login.stdout}\n${login.stderr}`.trim()
    if (!/logged in/i.test(loginStatus)) throw new Error(`Codex login is unavailable: ${loginStatus}`)
    const models = await codexModels(config.preflightTimeoutMs, config.evidencePreviewChars)
    const selected = models.find((model) => stringValue(model.id ?? model.model) === config.model.name)
    if (!selected) throw new Error(`Codex app-server does not advertise model ${config.model.name}`)
    const efforts = Array.isArray(selected.supportedReasoningEfforts) ? selected.supportedReasoningEfforts : []
    const effortIds = efforts.map((effort) => stringValue(recordValue(effort)?.reasoningEffort ?? recordValue(effort)?.effort ?? effort))
    if (effortIds.length > 0 && !effortIds.includes(config.model.reasoningEffort)) {
      throw new Error(`Codex model ${config.model.name} does not advertise reasoning effort ${config.model.reasoningEffort}`)
    }
    checks.model = { ok: true, provider: 'codex', name: config.model.name, reasoning_effort: config.model.reasoningEffort }
  } else {
    await openAiModelPreflight({
      baseUrl: config.model.baseUrl,
      model: config.model.name,
      apiKey: process.env.OPENAI_API_KEY!,
      timeoutMs: config.preflightTimeoutMs,
    })
    checks.model = {
      ok: true,
      provider: 'openai',
      name: config.model.name,
      reasoning_effort: config.model.reasoningEffort,
      note: 'authenticated model metadata lookup passed; no inference request sent',
    }
  }
  return checks
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runPreflight().then((checks) => process.stdout.write(`${JSON.stringify({ preflight: 'pass', checks }, null, 2)}\n`)).catch((error) => {
    process.stderr.write(`Preflight failed: ${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
