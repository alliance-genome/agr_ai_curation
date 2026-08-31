import path from 'node:path'

import { defineProjectSetup } from '@midscene/test/config'
import { PlaywrightAgent } from '@midscene/web/playwright'
import { chromium, type Response } from 'playwright'

import { ApiClient, type ApiEvidence } from './api-client.js'
import { applyProviderEnvironment, loadConfig } from './config.js'
import { ensureRunDirectories } from './evidence.js'
import { compactEvidence, redactSecrets } from './redaction.js'
import type { CaseState, SmokeContext, StreamCapture } from './smoke-context.js'
import { fileReadyCleanupIdentities, parseSseEvents, recordValue, stringValue } from './sse.js'

export function originScopedApiKeyHeaders(
  appUrl: string,
  requestUrl: string,
  current: Record<string, string>,
  apiKey: string,
): Record<string, string> {
  const headers = Object.fromEntries(Object.entries(current).filter(([name]) => name.toLowerCase() !== 'x-api-key'))
  if (new URL(requestUrl).origin === new URL(appUrl).origin) headers['X-API-Key'] = apiKey
  return headers
}

function cookieEntries(raw: string): Array<{ name: string; value: string }> {
  return raw.split(';').map((part) => part.trim()).filter(Boolean).map((part) => {
    const separator = part.indexOf('=')
    if (separator < 1) throw new Error('curator cookie must use name=value pairs')
    return { name: part.slice(0, separator).trim(), value: part.slice(separator + 1).trim() }
  })
}

function trackedPath(url: string): string | undefined {
  const parsed = new URL(url)
  const paths = ['/api/chat/session', '/api/chat/stream', '/api/chat/execute-flow', '/api/flows', '/api/weaviate/documents/upload']
  const exact = paths.find((candidate) => parsed.pathname === candidate)
  if (exact) return exact
  if (/^\/api\/flows\/[^/]+$/.test(parsed.pathname)) return parsed.pathname
  return undefined
}

function requestBody(response: Response): Record<string, unknown> {
  try { return recordValue(response.request().postDataJSON()) ?? {} } catch { return {} }
}

async function captureResponse(context: SmokeContext, state: CaseState | undefined, response: Response): Promise<void> {
  const pathName = trackedPath(response.url())
  if (!pathName || !state) return
  const method = response.request().method()
  if (!['POST', 'PUT', 'DELETE'].includes(method)) return
  const request = requestBody(response)
  const text = await response.text()
  if (pathName === '/api/chat/stream' || pathName === '/api/chat/execute-flow') {
    const events = parseSseEvents(text, context.config.evidencePreviewChars)
    const capture: StreamCapture = {
      path: pathName,
      request: redactSecrets(request) as Record<string, unknown>,
      status: response.status(),
      events,
    }
    state.streamCaptures.push(capture)
    for (const file of fileReadyCleanupIdentities(events)) {
      if (!state.resources.fileOutputs.some((item) => item.id === file.id)) {
        state.resources.fileOutputs.push(file)
      }
    }
    const sessionId = stringValue(request.session_id)
    if (sessionId) {
      state.chatSessionId = sessionId
      if (!state.resources.chatSessionIds.includes(sessionId)) state.resources.chatSessionIds.push(sessionId)
    }
    return
  }
  let body: unknown = text
  try { body = text ? JSON.parse(text) : null } catch { body = text.slice(0, context.config.evidencePreviewChars) }
  const evidence: ApiEvidence = {
    at: new Date().toISOString(), method, path: pathName, status: response.status(),
    request: redactSecrets(request), response: compactEvidence(body, context.config.evidencePreviewChars),
  }
  state.browserEvidence.push(evidence)
  const record = recordValue(body)
  if (!response.ok() || !record) return
  if (pathName === '/api/chat/session') {
    const id = stringValue(record.session_id)
    if (id && !state.resources.chatSessionIds.includes(id)) state.resources.chatSessionIds.push(id)
  } else if (pathName === '/api/flows') {
    const id = stringValue(record.id)
    if (id) {
      state.flowId = id
      if (!state.resources.flowIds.includes(id)) state.resources.flowIds.push(id)
    }
  } else if (pathName === '/api/weaviate/documents/upload') {
    const id = stringValue(record.document_id)
    if (id) {
      state.documentId = id
      if (!state.resources.documentIds.includes(id)) state.resources.documentIds.push(id)
    }
  }
}

export const smokeProjectSetup = defineProjectSetup<SmokeContext>({
  name: 'curator-smoke-browser',
  platform: 'web',
  async setup({ onTeardown }) {
    const config = loadConfig(process.env, { cwd: process.cwd(), requireSecrets: true })
    applyProviderEnvironment(config)
    process.env.MIDSCENE_RUN_DIR = path.join(config.runDir, 'midscene-reports')
    await ensureRunDirectories(config)
    const browser = await chromium.launch({ headless: config.headless, timeout: config.preflightTimeoutMs })
    const browserContext = await browser.newContext({
      viewport: { width: config.viewportWidth, height: config.viewportHeight },
    })
    if (config.appAuth === 'api-key') {
      await browserContext.route('**/*', async (route) => {
        await route.continue({
          headers: originScopedApiKeyHeaders(config.appUrl, route.request().url(), route.request().headers(), config.appSecret),
        })
      })
    }
    if (config.appAuth === 'cookie') {
      await browserContext.addCookies(cookieEntries(config.appSecret).map((cookie) => ({ ...cookie, url: config.appUrl })))
    }
    const page = await browserContext.newPage()
    const apiEvidence: ApiEvidence[] = []
    const context: SmokeContext = {
      config,
      browser,
      browserContext,
      page,
      api: new ApiClient({
        baseUrl: config.appUrl, authMode: config.appAuth, secret: config.appSecret,
        timeoutMs: config.httpTimeoutMs, evidence: apiEvidence,
        evidencePreviewChars: config.evidencePreviewChars,
      }),
      agents: new Map(),
      pendingCaptures: new Set(),
    }
    page.on('response', (response) => {
      const captureState = context.caseState
      const pending = captureResponse(context, captureState, response).catch((error) => {
        if (captureState) captureState.browserEvidence.push({
          at: new Date().toISOString(), method: response.request().method(), path: new URL(response.url()).pathname,
          status: response.status(), response: { capture_error: error instanceof Error ? error.message : String(error) },
        })
      }).finally(() => context.pendingCaptures.delete(pending))
      context.pendingCaptures.add(pending)
    })
    onTeardown(async () => {
      for (const agent of context.agents.values()) await agent.destroy()
      await browserContext.close()
      await browser.close()
    })
    return context
  },
})

export const smokeAgentProvider = {
  async getAgent(runId: string, { context }: { context: SmokeContext }): Promise<PlaywrightAgent> {
    activeContext = context
    const existing = context.agents.get(runId)
    if (existing) return existing
    const agent = new PlaywrightAgent(context.page, {
      testId: `agent-ui-smoke-${runId}`,
      reportFileName: `agent-ui-smoke-${runId}`,
      generateReport: true,
      persistExecutionDump: true,
      autoPrintReportMsg: false,
      screenshotShrinkFactor: context.config.screenshotShrinkFactor,
      replanningCycleLimit: context.config.replanningCycleLimit,
      waitAfterAction: context.config.waitAfterActionMs,
    })
    context.agents.set(runId, agent)
    return agent
  },
  async releaseAgent(runId: string): Promise<{ reportPath?: string }> {
    const context = activeContext
    const agent = context?.agents.get(runId)
    if (!agent) return {}
    await agent.destroy()
    context?.agents.delete(runId)
    return agent.reportFile ? { reportPath: agent.reportFile } : {}
  },
}

// The test runner invokes releaseAgent without passing project context.
let activeContext: SmokeContext | undefined

export function bindAgentProviderContext(context: SmokeContext): void {
  activeContext = context
}
