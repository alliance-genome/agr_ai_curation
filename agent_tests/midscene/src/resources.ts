import { ApiError } from './api-client.js'

export interface TrackedResources {
  loadedDocument: boolean
  fileOutputs: Array<{ id: string; filename: string }>
  chatSessionIds: string[]
  flowIds: string[]
  documentIds: string[]
}

export interface CleanupResult {
  attempted: string[]
  removed: string[]
  failures: string[]
  retained: boolean
}

export function emptyResources(): TrackedResources {
  return { loadedDocument: false, fileOutputs: [], chatSessionIds: [], flowIds: [], documentIds: [] }
}

async function retry(
  label: string,
  operation: () => Promise<unknown>,
  retryCount: number,
  retryIntervalMs: number,
): Promise<void> {
  let lastError: unknown
  for (let attempt = 0; attempt <= retryCount; attempt += 1) {
    try {
      await operation()
      return
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return
      lastError = error
      if (attempt < retryCount) await new Promise((resolve) => setTimeout(resolve, retryIntervalMs))
    }
  }
  throw new Error(`${label}: ${lastError instanceof Error ? lastError.message : String(lastError)}`)
}

export async function cleanupResources(options: {
  api: { delete(path: string): Promise<unknown> }
  removeFileOutput: (file: { id: string; filename: string }) => Promise<unknown>
  resources: TrackedResources
  retain: boolean
  retryCount: number
  retryIntervalMs: number
}): Promise<CleanupResult> {
  const result: CleanupResult = { attempted: [], removed: [], failures: [], retained: options.retain }
  if (options.retain) return result
  const operations: Array<[string, () => Promise<unknown>]> = []
  if (options.resources.loadedDocument) operations.push(['active-document', () => options.api.delete('/api/chat/document')])
  for (const file of [...options.resources.fileOutputs].reverse()) {
    operations.push([`file-output:${file.id}`, () => options.removeFileOutput(file)])
  }
  for (const id of [...options.resources.chatSessionIds].reverse()) {
    operations.push([`chat-session:${id}`, () => options.api.delete(`/api/chat/session/${encodeURIComponent(id)}`)])
  }
  for (const id of [...options.resources.flowIds].reverse()) {
    operations.push([`flow:${id}`, () => options.api.delete(`/api/flows/${encodeURIComponent(id)}`)])
  }
  for (const id of [...options.resources.documentIds].reverse()) {
    operations.push([`document:${id}`, () => options.api.delete(`/api/weaviate/documents/${encodeURIComponent(id)}`)])
  }
  for (const [label, operation] of operations) {
    result.attempted.push(label)
    try {
      await retry(label, operation, options.retryCount, options.retryIntervalMs)
      result.removed.push(label)
    } catch (error) {
      result.failures.push(error instanceof Error ? error.message : String(error))
    }
  }
  return result
}
