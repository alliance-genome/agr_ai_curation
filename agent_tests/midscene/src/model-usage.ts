import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'

import type { Provider } from './config.js'

type JsonRecord = Record<string, unknown>

interface UsageRecord {
  key: string
  model: string
  inputTokens: number
  cachedInputTokens: number
  cacheWriteInputTokens: number | null
  outputTokens: number
  totalTokens: number
}

interface UsageLocation {
  file: string
  value: JsonRecord
}

export const GPT_5_6_SOL_PRICING = {
  model: 'gpt-5.6-sol',
  asOf: '2026-08-30',
  source: 'https://developers.openai.com/api/docs/models/gpt-5.6-sol',
  longContextThresholdInputTokens: 272_000,
  shortContextUsdPerMillion: {
    input: 4,
    cachedInput: 0.4,
    cacheWriteInput: 5,
    output: 20,
  },
  longContextUsdPerMillion: {
    input: 8,
    cachedInput: 0.8,
    cacheWriteInput: 10,
    output: 30,
  },
} as const

function record(value: unknown): JsonRecord | undefined {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as JsonRecord
    : undefined
}

function tokenCount(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : undefined
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function collectUsageLocations(value: unknown, file: string, output: UsageLocation[]): void {
  if (Array.isArray(value)) {
    for (const item of value) collectUsageLocations(item, file, output)
    return
  }
  const object = record(value)
  if (!object) return
  const usage = record(object.usage)
  if (usage) output.push({ file, value: usage })
  for (const [key, nested] of Object.entries(object)) {
    if (key !== 'usage') collectUsageLocations(nested, file, output)
  }
}

function normalizeUsage(location: UsageLocation, configuredModel: string): UsageRecord | undefined {
  const value = location.value
  const requestId = stringValue(value.request_id)
  const callId = stringValue(value._midscene_call_id)
  if (!requestId && !callId) return undefined
  const inputTokens = tokenCount(value.inputTokens) ?? tokenCount(value.prompt_tokens) ?? 0
  const outputTokens = tokenCount(value.outputTokens) ?? tokenCount(value.completion_tokens) ?? 0
  const inputDetails = record(value.prompt_tokens_details) ?? record(value.input_tokens_details)
  const cacheWriteInputTokens = tokenCount(value.cacheWriteInputTokens)
    ?? tokenCount(inputDetails?.cache_write_tokens)
    ?? null
  return {
    key: requestId ? `request:${requestId}` : `call:${location.file}:${callId}`,
    model: stringValue(value.model_name) ?? configuredModel,
    inputTokens,
    cachedInputTokens: tokenCount(value.cachedInputTokens)
      ?? tokenCount(value.cached_input)
      ?? tokenCount(inputDetails?.cached_tokens)
      ?? 0,
    cacheWriteInputTokens,
    outputTokens,
    totalTokens: tokenCount(value.totalTokens) ?? tokenCount(value.total_tokens) ?? inputTokens + outputTokens,
  }
}

function sameUsage(left: UsageRecord, right: UsageRecord): boolean {
  return left.model === right.model
    && left.inputTokens === right.inputTokens
    && left.cachedInputTokens === right.cachedInputTokens
    && left.cacheWriteInputTokens === right.cacheWriteInputTokens
    && left.outputTokens === right.outputTokens
    && left.totalTokens === right.totalTokens
}

async function executionFiles(root: string): Promise<string[]> {
  try {
    const entries = await readdir(root, { withFileTypes: true })
    const nested = await Promise.all(entries.map(async (entry) => {
      const entryPath = path.join(root, entry.name)
      if (entry.isDirectory()) return executionFiles(entryPath)
      return entry.isFile() && entry.name.endsWith('.execution.json') ? [entryPath] : []
    }))
    return nested.flat().sort()
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []
    throw error
  }
}

function requestCostRange(usage: UsageRecord): { lower: number; upper: number } | undefined {
  if (usage.model !== GPT_5_6_SOL_PRICING.model) return undefined
  const rates = usage.inputTokens > GPT_5_6_SOL_PRICING.longContextThresholdInputTokens
    ? GPT_5_6_SOL_PRICING.longContextUsdPerMillion
    : GPT_5_6_SOL_PRICING.shortContextUsdPerMillion
  const knownCacheWrite = usage.cacheWriteInputTokens
  const nonCachedInput = Math.max(0, usage.inputTokens - usage.cachedInputTokens)
  const regularInput = knownCacheWrite === null
    ? nonCachedInput
    : Math.max(0, nonCachedInput - knownCacheWrite)
  const commonCost = usage.cachedInputTokens * rates.cachedInput + usage.outputTokens * rates.output
  const lower = (regularInput * rates.input + (knownCacheWrite ?? 0) * rates.cacheWriteInput + commonCost) / 1_000_000
  const upper = knownCacheWrite === null
    ? (nonCachedInput * rates.cacheWriteInput + commonCost) / 1_000_000
    : lower
  return { lower, upper }
}

export interface ModelUsageSummary {
  report_files: number
  request_count: number
  duplicate_usage_objects: number
  usage_objects_without_request_identity: number
  conflicting_request_ids: string[]
  parse_failures: string[]
  input_tokens: number
  cached_input_tokens: number
  reported_cache_write_input_tokens: number
  requests_missing_cache_write_tokens: number
  non_cached_input_tokens: number
  output_tokens: number
  total_tokens: number
  max_input_tokens_per_request: number
  requests_by_model: Record<string, number>
  pricing: typeof GPT_5_6_SOL_PRICING
  priced_requests: number
  unpriced_requests: number
  estimated_openai_api_cost_usd: number | null
  estimated_openai_api_cost_lower_bound_usd: number | null
  estimated_openai_api_cost_upper_bound_usd: number | null
  cost_estimate_complete: boolean
  cost_estimate_status: 'exact' | 'range_missing_cache_write_tokens' | 'unavailable'
  cost_estimate_unavailable_reasons: string[]
  billing_basis: 'codex_subscription_api_equivalent' | 'openai_api_estimate'
  cost_warning_usd: number
  cost_warning_exceeded: boolean | null
  cost_warning_status: 'not_applicable' | 'exceeded' | 'within_estimated_range' | 'unknown'
}

export async function summarizeModelUsage(
  reportDir: string,
  provider: Provider,
  configuredModel: string,
  costWarningUsd: number,
): Promise<ModelUsageSummary> {
  const files = await executionFiles(reportDir)
  const locations: UsageLocation[] = []
  const parseFailures: string[] = []
  for (const file of files) {
    try {
      collectUsageLocations(JSON.parse(await readFile(file, 'utf8')), path.relative(reportDir, file), locations)
    } catch {
      parseFailures.push(path.relative(reportDir, file))
    }
  }

  const requests = new Map<string, UsageRecord>()
  let withoutIdentity = 0
  let duplicateCount = 0
  const conflicts = new Set<string>()
  for (const location of locations) {
    const usage = normalizeUsage(location, configuredModel)
    if (!usage) {
      withoutIdentity += 1
      continue
    }
    const previous = requests.get(usage.key)
    if (previous) {
      duplicateCount += 1
      if (!sameUsage(previous, usage)) conflicts.add(usage.key)
      continue
    }
    requests.set(usage.key, usage)
  }

  const values = [...requests.values()]
  const sum = (selector: (usage: UsageRecord) => number): number => values.reduce((total, usage) => total + selector(usage), 0)
  const requestsByModel: Record<string, number> = {}
  let estimatedCostLower = 0
  let estimatedCostUpper = 0
  let pricedRequests = 0
  for (const usage of values) {
    requestsByModel[usage.model] = (requestsByModel[usage.model] ?? 0) + 1
    const cost = requestCostRange(usage)
    if (cost === undefined) continue
    pricedRequests += 1
    estimatedCostLower += cost.lower
    estimatedCostUpper += cost.upper
  }
  const inputTokens = sum((usage) => usage.inputTokens)
  const cachedInputTokens = sum((usage) => usage.cachedInputTokens)
  const cacheWriteInputTokens = sum((usage) => usage.cacheWriteInputTokens ?? 0)
  const missingCacheWriteTokens = values.filter((usage) => usage.cacheWriteInputTokens === null).length
  const roundedLowerCost = Number(estimatedCostLower.toFixed(6))
  const roundedUpperCost = Number(estimatedCostUpper.toFixed(6))
  const unavailableReasons: string[] = []
  if (files.length === 0) unavailableReasons.push('no_execution_reports')
  if (parseFailures.length > 0) unavailableReasons.push('execution_report_parse_failures')
  if (withoutIdentity > 0) unavailableReasons.push('usage_without_request_identity')
  if (conflicts.size > 0) unavailableReasons.push('conflicting_request_usage')
  if (pricedRequests < values.length) unavailableReasons.push('unpriced_models')
  if (files.length > 0 && values.length === 0) unavailableReasons.push('no_identified_model_requests')
  const boundsAvailable = unavailableReasons.length === 0
  const costEstimateComplete = boundsAvailable && missingCacheWriteTokens === 0
  const costEstimateStatus = !boundsAvailable
    ? 'unavailable' as const
    : missingCacheWriteTokens > 0
      ? 'range_missing_cache_write_tokens' as const
      : 'exact' as const
  const costWarningExceeded = provider === 'openai' && boundsAvailable
    ? estimatedCostUpper > costWarningUsd
    : null
  const costWarningStatus = provider !== 'openai'
    ? 'not_applicable' as const
    : !boundsAvailable
      ? 'unknown' as const
      : costWarningExceeded
        ? 'exceeded' as const
        : 'within_estimated_range' as const
  return {
    report_files: files.length,
    request_count: values.length,
    duplicate_usage_objects: duplicateCount,
    usage_objects_without_request_identity: withoutIdentity,
    conflicting_request_ids: [...conflicts].sort(),
    parse_failures: parseFailures,
    input_tokens: inputTokens,
    cached_input_tokens: cachedInputTokens,
    reported_cache_write_input_tokens: cacheWriteInputTokens,
    requests_missing_cache_write_tokens: missingCacheWriteTokens,
    non_cached_input_tokens: Math.max(0, inputTokens - cachedInputTokens),
    output_tokens: sum((usage) => usage.outputTokens),
    total_tokens: sum((usage) => usage.totalTokens),
    max_input_tokens_per_request: values.reduce((maximum, usage) => Math.max(maximum, usage.inputTokens), 0),
    requests_by_model: requestsByModel,
    pricing: GPT_5_6_SOL_PRICING,
    priced_requests: pricedRequests,
    unpriced_requests: values.length - pricedRequests,
    estimated_openai_api_cost_usd: costEstimateComplete ? roundedLowerCost : null,
    estimated_openai_api_cost_lower_bound_usd: boundsAvailable ? roundedLowerCost : null,
    estimated_openai_api_cost_upper_bound_usd: boundsAvailable ? roundedUpperCost : null,
    cost_estimate_complete: costEstimateComplete,
    cost_estimate_status: costEstimateStatus,
    cost_estimate_unavailable_reasons: unavailableReasons,
    billing_basis: provider === 'openai' ? 'openai_api_estimate' : 'codex_subscription_api_equivalent',
    cost_warning_usd: costWarningUsd,
    cost_warning_exceeded: costWarningExceeded,
    cost_warning_status: costWarningStatus,
  }
}
