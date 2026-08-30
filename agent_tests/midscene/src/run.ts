import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

import { runTestProject, type TestProjectRunResult } from '@midscene/test/config'

import { applyProviderEnvironment, loadConfig } from './config.js'
import { runPreflight } from './preflight.js'
import { redactSecrets } from './redaction.js'
import { createFreshRunDirectory } from './run-directory.js'
import { canonicalCaseStatuses, executedCanonicalCases } from './run-results.js'

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
- Started: ${verdict.started_at}
- Finished: ${verdict.finished_at}
- Result: ${verdict.result}
- Resources retained: ${verdict.resources_retained}
- Cleanup clean: ${verdict.cleanup_clean}

## Cases

${cases || '- No cases ran.'}

## Notes

This is an on-demand, local-only Midscene Beta pilot. It is not a CI or release gate.
Use each run's verdict and sanitized evidence to assess that invocation.
`
}

const config = loadConfig(process.env, { cwd: process.cwd(), requireSecrets: true })
applyProviderEnvironment(config)
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
const succeeded = !failure && result?.status === 'success' && cleanupClean && !config.retainResources
const verdict = redactSecrets({
  schema_version: 1,
  run_id: config.runId,
  started_at: startedAt,
  finished_at: new Date().toISOString(),
  result: succeeded ? 'pass' : config.retainResources && result?.status === 'success' ? 'partial' : 'fail',
  local_only: true,
  blocking_release_gate: false,
  midscene_beta: true,
  app_url: config.appUrl,
  app_auth: config.appAuth,
  model: {
    provider: config.provider,
    base_url: config.model.baseUrl,
    name: config.model.name,
    family: config.model.family,
    reasoning_enabled: config.model.reasoningEnabled,
    reasoning_effort: config.model.reasoningEffort,
    provider_retry_count: config.model.retryCount,
    whole_case_retry_count: config.caseRetryCount,
  },
  preflight,
  cases: caseStatuses,
  cleanup_clean: cleanupClean,
  resources_retained: config.retainResources,
  test_runner: result ? { result_dir: result.resultDir, summary_path: result.summaryPath, report_dir: result.reportDir, summary: result.summary } : null,
  failure: failure instanceof Error ? { name: failure.name, message: failure.message } : failure ? String(failure) : null,
}) as Record<string, any>
await writeFile(path.join(config.runDir, 'verdict.json'), `${JSON.stringify(verdict, null, 2)}\n`, { mode: 0o600 })
await writeFile(path.join(config.runDir, 'verdict.md'), markdownVerdict(verdict), { mode: 0o600 })
process.stdout.write(`Verdict: ${path.join(config.runDir, 'verdict.md')}\n`)
if (!succeeded) process.exitCode = 1
