import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { applyProviderEnvironment, loadConfig } from '../src/config.js'

const options = {
  cwd: '/repo/agent_tests/midscene',
  now: new Date('2026-08-30T12:00:00Z'),
  requireSecrets: false,
} as const

describe('smoke configuration', () => {
  it('uses the local Codex subscription defaults without an API key', () => {
    const config = loadConfig({}, options)
    assert.equal(config.appUrl, 'http://localhost:3002')
    assert.equal(config.provider, 'codex')
    assert.equal(config.model.baseUrl, 'codex://app-server')
    assert.equal(config.model.name, 'gpt-5.6-sol')
    assert.equal(config.model.reasoningEffort, 'low')
    assert.equal(config.model.retryCount, 1)
    assert.equal(config.caseRetryCount, 0)
    assert.equal(config.maxConcurrency, 1)
    assert.equal(config.openaiCostWarningUsd, 5)
    assert.equal(config.runPrefix, 'agent-smoke-20260830t120000z')
  })

  it('gives explicit environment values precedence over defaults', () => {
    const config = loadConfig({
      AGENT_UI_SMOKE_APP_URL: 'http://127.0.0.1:3999/',
      AGENT_UI_SMOKE_RUN_ID: 'manual-42',
      AGENT_UI_SMOKE_CASE: 'upload-ask,run-saved-flow',
      AGENT_UI_SMOKE_HEADLESS: 'false',
      AGENT_UI_SMOKE_TEST_TIMEOUT_MS: '12345',
      MIDSCENE_MODEL_NAME: 'gpt-5.6-terra',
      MIDSCENE_MODEL_RETRY_COUNT: '2',
      AGENT_UI_SMOKE_OPENAI_COST_WARNING_USD: '2.5',
    }, options)
    assert.equal(config.appUrl, 'http://127.0.0.1:3999')
    assert.equal(config.runId, 'manual-42')
    assert.deepEqual(config.cases, ['upload-ask', 'run-saved-flow'])
    assert.equal(config.headless, false)
    assert.equal(config.testTimeoutMs, 12345)
    assert.equal(config.model.name, 'gpt-5.6-terra')
    assert.equal(config.model.retryCount, 2)
    assert.equal(config.openaiCostWarningUsd, 2.5)
  })

  it('requires the selected application auth secret', () => {
    assert.throws(() => loadConfig({ AGENT_UI_SMOKE_APP_AUTH: 'cookie' }, { ...options, requireSecrets: true }), /CURATOR_COOKIE is required/)
  })

  it('rejects non-loopback targets and URL smuggling components', () => {
    const credentialedLoopbackUrl = new URL('http://localhost:3002')
    credentialedLoopbackUrl.username = 'user'
    credentialedLoopbackUrl.password = 'pass'
    for (const appUrl of [
      'https://shared-dev.example.org',
      credentialedLoopbackUrl.href,
      'http://localhost:3002/path',
      'http://localhost:3002?target=remote',
      'http://localhost:3002#fragment',
    ]) {
      assert.throws(() => loadConfig({ AGENT_UI_SMOKE_APP_URL: appUrl }, options), /loopback|localhost/)
    }
    assert.equal(loadConfig({ AGENT_UI_SMOKE_APP_URL: 'http://[::1]:3002' }, options).appUrl, 'http://[::1]:3002')
  })

  it('requires an OpenAI key only for the explicit OpenAI provider', () => {
    const shared = { TESTING_API_KEY: 'local-app-key' }
    assert.doesNotThrow(() => loadConfig(shared, { ...options, requireSecrets: true }))
    assert.throws(
      () => loadConfig({ ...shared, AGENT_UI_SMOKE_PROVIDER: 'openai' }, { ...options, requireSecrets: true }),
      /OPENAI_API_KEY is required only/,
    )
  })

  it('never installs an OpenAI billing key into the Codex provider environment', () => {
    const env: Record<string, string | undefined> = {
      OPENAI_API_KEY: 'sk-direct-billing-secret',
      MIDSCENE_MODEL_API_KEY: 'stale-secret',
    }
    const config = loadConfig(env, options)
    applyProviderEnvironment(config, env)
    assert.equal(env.MIDSCENE_MODEL_BASE_URL, 'codex://app-server')
    assert.equal(env.MIDSCENE_MODEL_API_KEY, undefined)
    assert.equal(env.OPENAI_API_KEY, 'sk-direct-billing-secret')
  })

  it('installs the key only when OpenAI is explicitly selected', () => {
    const env: Record<string, string | undefined> = {
      AGENT_UI_SMOKE_PROVIDER: 'openai',
      OPENAI_API_KEY: 'sk-direct-billing-secret',
    }
    const config = loadConfig(env, options)
    applyProviderEnvironment(config, env)
    assert.equal(env.MIDSCENE_MODEL_BASE_URL, 'https://api.openai.com/v1')
    assert.equal(env.MIDSCENE_MODEL_API_KEY, 'sk-direct-billing-secret')
  })

  it('rejects invalid operational limits and parallel execution', () => {
    assert.throws(() => loadConfig({ AGENT_UI_SMOKE_CASE_RETRY_COUNT: '-1' }, options))
    assert.throws(() => loadConfig({ AGENT_UI_SMOKE_MAX_CONCURRENCY: '2' }, options))
    assert.throws(() => loadConfig({ AGENT_UI_SMOKE_OPENAI_COST_WARNING_USD: '-0.01' }, options))
  })

  it('normalizes run IDs to the backend-stable 42-character ownership prefix', () => {
    assert.equal(loadConfig({ AGENT_UI_SMOKE_RUN_ID: 'Run..1--Part' }, options).runId, 'run-1-part')
    assert.equal(loadConfig({ AGENT_UI_SMOKE_RUN_ID: 'a'.repeat(42) }, options).runId, 'a'.repeat(42))
    assert.throws(() => loadConfig({ AGENT_UI_SMOKE_RUN_ID: 'a'.repeat(43) }, options), /1-42/)
  })
})
