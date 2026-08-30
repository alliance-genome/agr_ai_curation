import type { ModelUsageSummary } from './model-usage.js'
import { redactSecrets } from './redaction.js'

export function buildRedactedVerdict(
  value: Record<string, unknown>,
  modelUsage: ModelUsageSummary,
): Record<string, unknown> {
  const redacted = redactSecrets(value) as Record<string, unknown>
  return { ...redacted, model_usage: modelUsage }
}
