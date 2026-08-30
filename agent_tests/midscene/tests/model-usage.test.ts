import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { describe, it } from 'node:test'

import { summarizeModelUsage } from '../src/model-usage.js'

async function reportDirectory(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), 'midscene-usage-'))
  const report = path.join(root, 'nested')
  await mkdir(report)
  return report
}

describe('Midscene model usage accounting', () => {
  it('deduplicates request IDs and estimates short- and long-context API cost', async () => {
    const report = await reportDirectory()
    const first = {
      request_id: 'req-short',
      inputTokens: 200_000,
      cachedInputTokens: 50_000,
      cacheWriteInputTokens: 10_000,
      outputTokens: 10_000,
      totalTokens: 1_010_000,
      model_name: 'gpt-5.6-sol',
    }
    const second = {
      request_id: 'req-long',
      prompt_tokens: 300_000,
      prompt_tokens_details: { cached_tokens: 100_000, cache_write_tokens: 50_000 },
      completion_tokens: 2_000,
      total_tokens: 302_000,
      model_name: 'gpt-5.6-sol',
    }
    await writeFile(path.join(report, 'one.execution.json'), JSON.stringify({ tasks: [{ usage: first }, { repeated: { usage: first } }] }))
    await writeFile(path.join(report, 'two.execution.json'), JSON.stringify({ usage: second }))

    const summary = await summarizeModelUsage(path.dirname(report), 'openai', 'gpt-5.6-sol', 1)

    assert.equal(summary.report_files, 2)
    assert.equal(summary.request_count, 2)
    assert.equal(summary.duplicate_usage_objects, 1)
    assert.equal(summary.input_tokens, 500_000)
    assert.equal(summary.cached_input_tokens, 150_000)
    assert.equal(summary.reported_cache_write_input_tokens, 60_000)
    assert.equal(summary.requests_missing_cache_write_tokens, 0)
    assert.equal(summary.non_cached_input_tokens, 350_000)
    assert.equal(summary.output_tokens, 12_000)
    assert.equal(summary.max_input_tokens_per_request, 300_000)
    assert.equal(summary.estimated_openai_api_cost_usd, 2.67)
    assert.equal(summary.estimated_openai_api_cost_lower_bound_usd, 2.67)
    assert.equal(summary.estimated_openai_api_cost_upper_bound_usd, 2.67)
    assert.equal(summary.cost_estimate_complete, true)
    assert.equal(summary.cost_warning_exceeded, true)
    assert.equal(summary.cost_warning_status, 'exceeded')
  })

  it('keeps token totals but leaves unknown models unpriced', async () => {
    const report = await reportDirectory()
    await writeFile(path.join(report, 'unknown.execution.json'), JSON.stringify({
      usage: {
        request_id: 'req-unknown', inputTokens: 10, outputTokens: 2, totalTokens: 12, model_name: 'future-model',
      },
    }))
    const summary = await summarizeModelUsage(report, 'codex', 'future-model', 0)
    assert.equal(summary.total_tokens, 12)
    assert.equal(summary.priced_requests, 0)
    assert.equal(summary.unpriced_requests, 1)
    assert.equal(summary.estimated_openai_api_cost_usd, null)
    assert.equal(summary.estimated_openai_api_cost_lower_bound_usd, null)
    assert.equal(summary.estimated_openai_api_cost_upper_bound_usd, null)
    assert.equal(summary.cost_estimate_complete, false)
    assert.equal(summary.cost_estimate_status, 'unavailable')
    assert.deepEqual(summary.cost_estimate_unavailable_reasons, ['unpriced_models'])
    assert.equal(summary.billing_basis, 'codex_subscription_api_equivalent')
    assert.equal(summary.cost_warning_exceeded, null)
    assert.equal(summary.cost_warning_status, 'not_applicable')
  })

  it('returns zero usage when no Midscene execution report exists', async () => {
    const summary = await summarizeModelUsage('/definitely/missing/midscene-report', 'openai', 'gpt-5.6-sol', 5)
    assert.equal(summary.report_files, 0)
    assert.equal(summary.request_count, 0)
    assert.equal(summary.estimated_openai_api_cost_usd, null)
    assert.equal(summary.estimated_openai_api_cost_lower_bound_usd, null)
    assert.equal(summary.estimated_openai_api_cost_upper_bound_usd, null)
    assert.equal(summary.cost_warning_status, 'unknown')
    assert.deepEqual(summary.cost_estimate_unavailable_reasons, ['no_execution_reports'])
  })

  it('uses a conservative cost range when Midscene drops direct-OpenAI cache-write detail', async () => {
    const report = await reportDirectory()
    await writeFile(path.join(report, 'openai.execution.json'), JSON.stringify({
      usage: {
        request_id: 'req-openai',
        prompt_tokens: 1_000,
        completion_tokens: 100,
        total_tokens: 1_100,
        cached_input: 200,
        model_name: 'gpt-5.6-sol',
      },
    }))
    const summary = await summarizeModelUsage(report, 'openai', 'gpt-5.6-sol', 0.0055)

    assert.equal(summary.requests_missing_cache_write_tokens, 1)
    assert.equal(summary.estimated_openai_api_cost_usd, null)
    assert.equal(summary.estimated_openai_api_cost_lower_bound_usd, 0.00528)
    assert.equal(summary.estimated_openai_api_cost_upper_bound_usd, 0.00608)
    assert.equal(summary.cost_estimate_complete, false)
    assert.equal(summary.cost_warning_exceeded, true)
    assert.equal(summary.cost_estimate_status, 'range_missing_cache_write_tokens')
    assert.equal(summary.cost_warning_status, 'exceeded')
  })
})
