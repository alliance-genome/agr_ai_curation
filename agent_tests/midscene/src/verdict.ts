import type { ModelUsageSummary } from './model-usage.js'
import { compactDiagnostic, redactSecrets } from './redaction.js'

export function verdictFailure(failure: unknown, evidencePreviewChars: number): unknown {
  if (failure instanceof Error) {
    return { name: failure.name, message: compactDiagnostic(failure.message, evidencePreviewChars) }
  }
  return failure ? compactDiagnostic(failure, evidencePreviewChars) : null
}

export function buildRedactedVerdict(
  value: Record<string, unknown>,
  modelUsage: ModelUsageSummary,
): Record<string, unknown> {
  const redacted = redactSecrets(value) as Record<string, unknown>
  return { ...redacted, model_usage: modelUsage }
}
