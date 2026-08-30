export interface PollOptions {
  label: string
  intervalMs: number
  limit: number
  signal?: AbortSignal
}

export async function pollUntil<T>(
  operation: (attempt: number) => Promise<T>,
  accept: (value: T) => boolean,
  options: PollOptions,
): Promise<T> {
  let lastValue: T | undefined
  for (let attempt = 1; attempt <= options.limit; attempt += 1) {
    if (options.signal?.aborted) throw options.signal.reason
    lastValue = await operation(attempt)
    if (accept(lastValue)) return lastValue
    if (attempt < options.limit) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, options.intervalMs)
        options.signal?.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(options.signal?.reason)
        }, { once: true })
      })
    }
  }
  throw new Error(`${options.label} did not succeed after ${options.limit} attempts; last value: ${safePreview(lastValue)}`)
}

function safePreview(value: unknown): string {
  try {
    return JSON.stringify(value)?.slice(0, 500) ?? String(value)
  } catch {
    return '[unserializable]'
  }
}
