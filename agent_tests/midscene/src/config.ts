import path from 'node:path'

import { z } from 'zod'

export const CASE_NAMES = ['create-connect-save', 'edit-rewire', 'upload-ask', 'run-saved-flow'] as const
export type CaseName = (typeof CASE_NAMES)[number]
export type Provider = 'codex' | 'openai'
export type AppAuthMode = 'api-key' | 'cookie'

type Environment = NodeJS.ProcessEnv | Record<string, string | undefined>

const booleanSchema = z.enum(['true', 'false'])
const providerSchema = z.enum(['codex', 'openai'])
const authSchema = z.enum(['api-key', 'cookie'])

const DEFAULTS = {
  appUrl: 'http://localhost:3002',
  provider: 'codex' as Provider,
  appAuth: 'api-key' as AppAuthMode,
  apiKeyEnv: 'TESTING_API_KEY',
  cookieEnv: 'CURATOR_COOKIE',
  headless: true,
  retainResources: false,
  caseRetryCount: 0,
  maxConcurrency: 1,
  testTimeoutMs: 900_000,
  stepTimeoutMs: 180_000,
  httpTimeoutMs: 30_000,
  uploadTimeoutMs: 180_000,
  pdfProcessingTimeoutMs: 600_000,
  pdfPollIntervalMs: 3_000,
  pdfPollLimit: 200,
  persistencePollIntervalMs: 1_000,
  persistencePollLimit: 60,
  cleanupRetryCount: 1,
  cleanupRetryIntervalMs: 1_000,
  cleanupCommandTimeoutMs: 30_000,
  captureDrainTimeoutMs: 180_000,
  preflightTimeoutMs: 15_000,
  viewportWidth: 1440,
  viewportHeight: 900,
  screenshotShrinkFactor: 1,
  replanningCycleLimit: 24,
  waitAfterActionMs: 300,
  evidencePreviewChars: 4_000,
  codexBaseUrl: 'codex://app-server',
  codexModel: 'gpt-5.6-sol',
  codexFamily: 'gpt-5',
  openaiBaseUrl: 'https://api.openai.com/v1',
  openaiModel: 'gpt-5.6-sol',
  reasoningEnabled: true,
  reasoningEffort: 'low',
  modelTimeoutMs: 300_000,
  modelRetryCount: 1,
  modelRetryIntervalMs: 1_000,
} as const

function readBoolean(env: Environment, name: string, fallback: boolean): boolean {
  const raw = env[name]
  return raw === undefined ? fallback : booleanSchema.parse(raw.trim().toLowerCase()) === 'true'
}

function readInteger(
  env: Environment,
  name: string,
  fallback: number,
  options: { min: number; max?: number },
): number {
  const raw = env[name]
  if (raw === undefined || raw.trim() === '') return fallback
  const parsed = z.coerce.number().int().min(options.min)
  return (options.max === undefined ? parsed : parsed.max(options.max)).parse(raw)
}

function readNonEmpty(env: Environment, name: string, fallback: string): string {
  const raw = env[name]?.trim()
  return raw || fallback
}

function normalizeRunId(raw: string): string {
  const normalized = raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  if (!normalized || normalized.length > 42 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(normalized)) {
    throw new Error('AGENT_UI_SMOKE_RUN_ID must become 1-42 lowercase alphanumeric characters with single hyphen separators')
  }
  return normalized
}

function defaultRunId(now: Date): string {
  return now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z').toLowerCase()
}

export function localAppUrl(raw: string): string {
  const url = new URL(raw)
  const loopbackHosts = new Set(['localhost', '127.0.0.1', '[::1]', '::1'])
  if (!['http:', 'https:'].includes(url.protocol) || !loopbackHosts.has(url.hostname)) {
    throw new Error('AGENT_UI_SMOKE_APP_URL must use HTTP(S) on localhost, 127.0.0.1, or [::1]')
  }
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('AGENT_UI_SMOKE_APP_URL must be a loopback origin without credentials, path, query, or fragment')
  }
  return url.toString().replace(/\/$/, '')
}

function selectedCases(env: Environment): CaseName[] {
  const raw = readNonEmpty(env, 'AGENT_UI_SMOKE_CASE', 'all')
  if (raw === 'all') return [...CASE_NAMES]
  const values = raw.split(',').map((value) => value.trim()).filter(Boolean)
  const parsed = z.array(z.enum(CASE_NAMES)).min(1).parse(values)
  return [...new Set(parsed)]
}

export interface SmokeConfig {
  repoRoot: string
  packageRoot: string
  outputRoot: string
  runId: string
  runPrefix: string
  runDir: string
  appUrl: string
  provider: Provider
  appAuth: AppAuthMode
  apiKeyEnv: string
  cookieEnv: string
  appSecret: string
  headless: boolean
  retainResources: boolean
  cases: CaseName[]
  tags: string[]
  caseRetryCount: number
  maxConcurrency: number
  testTimeoutMs: number
  stepTimeoutMs: number
  httpTimeoutMs: number
  uploadTimeoutMs: number
  pdfProcessingTimeoutMs: number
  pdfPollIntervalMs: number
  pdfPollLimit: number
  persistencePollIntervalMs: number
  persistencePollLimit: number
  cleanupRetryCount: number
  cleanupRetryIntervalMs: number
  cleanupCommandTimeoutMs: number
  captureDrainTimeoutMs: number
  preflightTimeoutMs: number
  viewportWidth: number
  viewportHeight: number
  screenshotShrinkFactor: number
  replanningCycleLimit: number
  waitAfterActionMs: number
  evidencePreviewChars: number
  model: {
    baseUrl: string
    name: string
    family: string
    reasoningEnabled: boolean
    reasoningEffort: string
    timeoutMs: number
    retryCount: number
    retryIntervalMs: number
  }
}

export function loadConfig(
  env: Environment = process.env,
  options: { cwd?: string; now?: Date; requireSecrets?: boolean } = {},
): SmokeConfig {
  const packageRoot = path.resolve(options.cwd ?? process.cwd())
  const repoRoot = path.resolve(packageRoot, '../..')
  const now = options.now ?? new Date()
  const runId = normalizeRunId(readNonEmpty(env, 'AGENT_UI_SMOKE_RUN_ID', defaultRunId(now)))
  const provider = providerSchema.parse(readNonEmpty(env, 'AGENT_UI_SMOKE_PROVIDER', DEFAULTS.provider))
  const appAuth = authSchema.parse(readNonEmpty(env, 'AGENT_UI_SMOKE_APP_AUTH', DEFAULTS.appAuth))
  const apiKeyEnv = readNonEmpty(env, 'AGENT_UI_SMOKE_API_KEY_ENV', DEFAULTS.apiKeyEnv)
  const cookieEnv = readNonEmpty(env, 'AGENT_UI_SMOKE_COOKIE_ENV', DEFAULTS.cookieEnv)
  const secretEnvName = appAuth === 'api-key' ? apiKeyEnv : cookieEnv
  const appSecret = env[secretEnvName]?.trim() ?? ''
  if (options.requireSecrets !== false && !appSecret) {
    throw new Error(`${secretEnvName} is required for ${appAuth} application authentication`)
  }
  if (options.requireSecrets !== false && provider === 'openai' && !env.OPENAI_API_KEY?.trim()) {
    throw new Error('OPENAI_API_KEY is required only when AGENT_UI_SMOKE_PROVIDER=openai')
  }

  const outputRoot = path.resolve(
    repoRoot,
    readNonEmpty(env, 'AGENT_UI_SMOKE_OUTPUT_ROOT', 'file_outputs/temp/agent_ui_smoke'),
  )
  const modelBaseUrl = provider === 'codex'
    ? readNonEmpty(env, 'MIDSCENE_MODEL_BASE_URL', DEFAULTS.codexBaseUrl)
    : readNonEmpty(env, 'AGENT_UI_SMOKE_OPENAI_BASE_URL', DEFAULTS.openaiBaseUrl)
  const modelName = provider === 'codex'
    ? readNonEmpty(env, 'MIDSCENE_MODEL_NAME', DEFAULTS.codexModel)
    : readNonEmpty(env, 'AGENT_UI_SMOKE_OPENAI_MODEL', DEFAULTS.openaiModel)

  return {
    repoRoot,
    packageRoot,
    outputRoot,
    runId,
    runPrefix: `agent-smoke-${runId}`,
    runDir: path.join(outputRoot, runId),
    appUrl: localAppUrl(readNonEmpty(env, 'AGENT_UI_SMOKE_APP_URL', DEFAULTS.appUrl)),
    provider,
    appAuth,
    apiKeyEnv,
    cookieEnv,
    appSecret,
    headless: readBoolean(env, 'AGENT_UI_SMOKE_HEADLESS', DEFAULTS.headless),
    retainResources: readBoolean(env, 'AGENT_UI_SMOKE_RETAIN_RESOURCES', DEFAULTS.retainResources),
    cases: selectedCases(env),
    tags: (env.AGENT_UI_SMOKE_TAGS ?? '').split(',').map((tag) => tag.trim()).filter(Boolean),
    caseRetryCount: readInteger(env, 'AGENT_UI_SMOKE_CASE_RETRY_COUNT', DEFAULTS.caseRetryCount, { min: 0, max: 3 }),
    maxConcurrency: readInteger(env, 'AGENT_UI_SMOKE_MAX_CONCURRENCY', DEFAULTS.maxConcurrency, { min: 1, max: 1 }),
    testTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_TEST_TIMEOUT_MS', DEFAULTS.testTimeoutMs, { min: 1 }),
    stepTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_STEP_TIMEOUT_MS', DEFAULTS.stepTimeoutMs, { min: 1 }),
    httpTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_HTTP_TIMEOUT_MS', DEFAULTS.httpTimeoutMs, { min: 1 }),
    uploadTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_UPLOAD_TIMEOUT_MS', DEFAULTS.uploadTimeoutMs, { min: 1 }),
    pdfProcessingTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_PDF_PROCESSING_TIMEOUT_MS', DEFAULTS.pdfProcessingTimeoutMs, { min: 1 }),
    pdfPollIntervalMs: readInteger(env, 'AGENT_UI_SMOKE_PDF_POLL_INTERVAL_MS', DEFAULTS.pdfPollIntervalMs, { min: 1 }),
    pdfPollLimit: readInteger(env, 'AGENT_UI_SMOKE_PDF_POLL_LIMIT', DEFAULTS.pdfPollLimit, { min: 1 }),
    persistencePollIntervalMs: readInteger(env, 'AGENT_UI_SMOKE_PERSISTENCE_POLL_INTERVAL_MS', DEFAULTS.persistencePollIntervalMs, { min: 1 }),
    persistencePollLimit: readInteger(env, 'AGENT_UI_SMOKE_PERSISTENCE_POLL_LIMIT', DEFAULTS.persistencePollLimit, { min: 1 }),
    cleanupRetryCount: readInteger(env, 'AGENT_UI_SMOKE_CLEANUP_RETRY_COUNT', DEFAULTS.cleanupRetryCount, { min: 0, max: 10 }),
    cleanupRetryIntervalMs: readInteger(env, 'AGENT_UI_SMOKE_CLEANUP_RETRY_INTERVAL_MS', DEFAULTS.cleanupRetryIntervalMs, { min: 1 }),
    cleanupCommandTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_CLEANUP_COMMAND_TIMEOUT_MS', DEFAULTS.cleanupCommandTimeoutMs, { min: 1 }),
    captureDrainTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_CAPTURE_DRAIN_TIMEOUT_MS', DEFAULTS.captureDrainTimeoutMs, { min: 1 }),
    preflightTimeoutMs: readInteger(env, 'AGENT_UI_SMOKE_PREFLIGHT_TIMEOUT_MS', DEFAULTS.preflightTimeoutMs, { min: 1 }),
    viewportWidth: readInteger(env, 'AGENT_UI_SMOKE_VIEWPORT_WIDTH', DEFAULTS.viewportWidth, { min: 320 }),
    viewportHeight: readInteger(env, 'AGENT_UI_SMOKE_VIEWPORT_HEIGHT', DEFAULTS.viewportHeight, { min: 240 }),
    screenshotShrinkFactor: readInteger(env, 'AGENT_UI_SMOKE_SCREENSHOT_SHRINK_FACTOR', DEFAULTS.screenshotShrinkFactor, { min: 1, max: 4 }),
    replanningCycleLimit: readInteger(env, 'AGENT_UI_SMOKE_REPLANNING_CYCLE_LIMIT', DEFAULTS.replanningCycleLimit, { min: 1 }),
    waitAfterActionMs: readInteger(env, 'AGENT_UI_SMOKE_WAIT_AFTER_ACTION_MS', DEFAULTS.waitAfterActionMs, { min: 0 }),
    evidencePreviewChars: readInteger(env, 'AGENT_UI_SMOKE_EVIDENCE_PREVIEW_CHARS', DEFAULTS.evidencePreviewChars, { min: 100 }),
    model: {
      baseUrl: modelBaseUrl,
      name: modelName,
      family: readNonEmpty(env, 'MIDSCENE_MODEL_FAMILY', DEFAULTS.codexFamily),
      reasoningEnabled: readBoolean(env, 'MIDSCENE_MODEL_REASONING_ENABLED', DEFAULTS.reasoningEnabled),
      reasoningEffort: readNonEmpty(env, 'MIDSCENE_MODEL_REASONING_EFFORT', DEFAULTS.reasoningEffort),
      timeoutMs: readInteger(env, 'MIDSCENE_MODEL_TIMEOUT', DEFAULTS.modelTimeoutMs, { min: 1 }),
      retryCount: readInteger(env, 'MIDSCENE_MODEL_RETRY_COUNT', DEFAULTS.modelRetryCount, { min: 0, max: 10 }),
      retryIntervalMs: readInteger(env, 'MIDSCENE_MODEL_RETRY_INTERVAL', DEFAULTS.modelRetryIntervalMs, { min: 1 }),
    },
  }
}

export function applyProviderEnvironment(config: SmokeConfig, env: Environment = process.env): void {
  env.MIDSCENE_MODEL_BASE_URL = config.model.baseUrl
  env.MIDSCENE_MODEL_NAME = config.model.name
  env.MIDSCENE_MODEL_FAMILY = config.model.family
  env.MIDSCENE_MODEL_REASONING_ENABLED = String(config.model.reasoningEnabled)
  env.MIDSCENE_MODEL_REASONING_EFFORT = config.model.reasoningEffort
  env.MIDSCENE_MODEL_TIMEOUT = String(config.model.timeoutMs)
  env.MIDSCENE_MODEL_RETRY_COUNT = String(config.model.retryCount)
  env.MIDSCENE_MODEL_RETRY_INTERVAL = String(config.model.retryIntervalMs)
  if (config.provider === 'codex') {
    delete env.MIDSCENE_MODEL_API_KEY
  } else {
    env.MIDSCENE_MODEL_API_KEY = env.OPENAI_API_KEY
  }
}

export const CONFIG_DEFAULTS = DEFAULTS
