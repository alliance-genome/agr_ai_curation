let fallbackTurnCounter = 0

export function buildTurnId(): string {
  const cryptoApi = globalThis.crypto
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    try {
      return cryptoApi.randomUUID()
    } catch {
      // Fall through for embedded or insecure browser contexts.
    }
  }

  fallbackTurnCounter += 1
  return `turn-${Date.now().toString(36)}-${fallbackTurnCounter.toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
