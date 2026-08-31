import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

import { runTestProject, type TestProjectRunResult } from '@midscene/test/config'

import { applyProviderEnvironment, loadConfig, pinRunId } from './config.js'
import { runPreflight } from './preflight.js'
import { createFreshRunDirectory } from './run-directory.js'
import { acceptanceCases, canonicalCaseStatuses, executedCanonicalCases, runAcceptancePassed, selectedCasesSucceeded } from './run-results.js'
import { readRunnerProvenance } from './provenance.js'
import { summarizeModelUsage } from './model-usage.js'
import { buildRedactedVerdict, verdictFailure } from './verdict.js'

async function cleanupIsClean(config: ReturnType<typeof loadConfig>, runResult: TestProjectRunResult | undefined): Promise<boolean> {
  const executedCases = executedCanonicalCases(runResult)
  if (executedCases.length === 0) return false
  for (const caseName of executedCases) {
    try {
      const raw = JSON.parse(await readFile(path.join(config.runDir, 'api-evidence', `${caseName}.json`), 'utf8')) as Record<string, unknown>
      const cleanup = raw.cleanup as { failures?: unknown[]; retained?: boolean } | undefined
      if (!cleanup || cleanup.retained || (cleanup.failures?.length ?? 0) > 0) return false
    } catch { return false }
  }
  return true
}

function markdownVerdict(verdict: Record<string, any>): string {
  const cases = Object.entries(verdict.cases ?? {}).map(([name, status]) => `- ${name}: ${String(status)}`).join('\n')
  return `# Midscene Curator-Agent Smoke Verdict

- Run ID: ${verdict.run_id}
- Environment: local Docker development stack
- Provider: ${verdict.model.provider} / ${verdict.model.name}
- Runner Git SHA: ${verdict.runner.git_sha}
- Runner hostname: ${verdict.runner.hostname}
- Started: ${verdict.started_at}
- Finished: ${verdict.finished_at}
- Result: ${verdict.result}
- Resources retained: ${verdict.resources_retained}
- Cleanup clean: ${verdict.cleanup_clean}

## Model usage

- Requests: ${verdict.model_usage.request_count}
- Input tokens: ${verdict.model_usage.input_tokens}
- Cached input tokens: ${verdict.model_usage.cached_input_tokens}
- Reported cache-write input tokens: ${verdict.model_usage.reported_cache_write_input_tokens}
- Requests missing cache-write detail: ${verdict.model_usage.requests_missing_cache_write_tokens}
- Output tokens: ${verdict.model_usage.output_tokens}
- Cost estimate status: ${verdict.model_usage.cost_estimate_status}
- Estimated OpenAI API cost: ${verdict.model_usage.estimated_openai_api_cost_usd === null ? 'not exact' : `$${Number(verdict.model_usage.estimated_openai_api_cost_usd).toFixed(6)}`}
- Estimated OpenAI API cost range: ${verdict.model_usage.estimated_openai_api_cost_lower_bound_usd === null ? 'unavailable' : `$${Number(verdict.model_usage.estimated_openai_api_cost_lower_bound_usd).toFixed(6)}–$${Number(verdict.model_usage.estimated_openai_api_cost_upper_bound_usd).toFixed(6)}`}
- Cost estimate unavailable reasons: ${verdict.model_usage.cost_estimate_unavailable_reasons.join(', ') || 'none'}
- Billing basis: ${verdict.model_usage.billing_basis}
- OpenAI cost warning status: ${verdict.model_usage.cost_warning_status}

## Cases

${cases || '- No cases ran.'}

## Notes

This is an on-demand, local-only Midscene Beta pilot. It is not a CI or release gate.
Use each run's verdict and sanitized evidence to assess that invocation.
`
}

const config = loadConfig(process.env, { cwd: process.cwd(), requireSecrets: true })
pinRunId(config)
applyProviderEnvironment(config)
const runner = await readRunnerProvenance(config.repoRoot, config.preflightTimeoutMs)
await createFreshRunDirectory(config.outputRoot, config.runDir, config.runId)
const startedAt = new Date().toISOString()
let result: TestProjectRunResult | undefined
let failure: unknown
let preflight: unknown
try {
  preflight = await runPreflight()
  result = await runTestProject({
    cwd: config.packageRoot,
    projectRoot: config.packageRoot,
    configPath: path.join(config.packageRoot, 'midscene.config.ts'),
    resultDir: path.join(config.runDir, 'test-runner'),
    onProgress: (message) => process.stdout.write(`${message}\n`),
  })
} catch (error) {
  failure = error
}

const canonicalStatuses = canonicalCaseStatuses(result)
const caseStatuses = Object.fromEntries(config.cases.map((caseName) => [caseName, canonicalStatuses[caseName]]))
const cleanupClean = await cleanupIsClean(config, result)
const modelUsage = await summarizeModelUsage(
  path.join(config.runDir, 'midscene-reports', 'report'),
  config.provider,
  config.model.name,
  config.openaiCostWarningUsd,
)
const evaluatedCases = acceptanceCases(result, config.cases, config.tags)
const allSelectedCasesSucceeded = evaluatedCases.length > 0
  && selectedCasesSucceeded(canonicalStatuses, evaluatedCases)
const acceptancePassed = runAcceptancePassed(canonicalStatuses, evaluatedCases, modelUsage.request_count)
const executionSucceeded = !failure && result?.status === 'success' && acceptancePassed
const succeeded = executionSucceeded && cleanupClean && !config.retainResources
const verdict = buildRedactedVerdict({
  schema_version: 2,
  run_id: config.runId,
  started_at: startedAt,
  finished_at: new Date().toISOString(),
  result: succeeded ? 'pass' : config.retainResources && executionSucceeded ? 'partial' : 'fail',
  local_only: true,
  blocking_release_gate: false,
  midscene_beta: true,
  app_url: config.appUrl,
  app_auth: config.appAuth,
  runner,
  model: {
    provider: config.provider,
    base_url: config.model.baseUrl,
    name: config.model.name,
    family: config.model.family,
    reasoning_enabled: config.model.reasoningEnabled,
    reasoning_effort: config.model.reasoningEffort,
    temperature: config.model.temperature,
    provider_retry_count: config.model.retryCount,
    whole_case_retry_count: config.caseRetryCount,
  },
  preflight,
  cases: caseStatuses,
  acceptance: {
    evaluated_cases: evaluatedCases,
    all_selected_cases_succeeded: allSelectedCasesSucceeded,
    identified_model_requests: modelUsage.request_count,
    passed: acceptancePassed,
  },
  cleanup_clean: cleanupClean,
  resources_retained: config.retainResources,
  test_runner: result ? { result_dir: result.resultDir, summary_path: result.summaryPath, report_dir: result.reportDir, summary: result.summary } : null,
  failure: verdictFailure(failure, config.evidencePreviewChars),
}, modelUsage) as Record<string, any>
await writeFile(path.join(config.runDir, 'verdict.json'), `${JSON.stringify(verdict, null, 2)}\n`, { mode: 0o600 })
await writeFile(path.join(config.runDir, 'verdict.md'), markdownVerdict(verdict), { mode: 0o600 })
process.stdout.write(`Verdict: ${path.join(config.runDir, 'verdict.md')}\n`)
if (modelUsage.cost_warning_exceeded && modelUsage.estimated_openai_api_cost_upper_bound_usd !== null) {
  process.stderr.write(
    `Warning: estimated OpenAI API cost upper bound $${modelUsage.estimated_openai_api_cost_upper_bound_usd.toFixed(6)} exceeds `
      + `$${modelUsage.cost_warning_usd.toFixed(2)}; this is an after-run warning, not a hard billing cap.\n`,
  )
} else if (modelUsage.cost_warning_status === 'unknown') {
  process.stderr.write(
    `Warning: OpenAI API cost could not be bounded: ${modelUsage.cost_estimate_unavailable_reasons.join(', ')}.\n`,
  )
}
if (!succeeded) process.exitCode = 1
