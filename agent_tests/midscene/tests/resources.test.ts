import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { cleanupResources } from '../src/resources.js'

describe('resource cleanup', () => {
  it('runs teardown in dependency-safe order, reversing resources within a kind', async () => {
    const calls: string[] = []
    const result = await cleanupResources({
      api: { delete: async (path: string) => { calls.push(path); return null } },
      removeFileOutput: async (file) => { calls.push(`/local-file/${file.id}`) },
      resources: {
        loadedDocument: true,
        fileOutputs: [{ id: 'file-1', filename: 'agent-smoke-run-file.json' }],
        chatSessionIds: ['session-1', 'session-2'],
        flowIds: ['flow-1', 'flow-2'],
        documentIds: ['document-1', 'document-2'],
      },
      retain: false,
      retryCount: 0,
      retryIntervalMs: 1,
    })
    assert.deepEqual(calls, [
      '/api/chat/document',
      '/local-file/file-1',
      '/api/chat/session/session-2',
      '/api/chat/session/session-1',
      '/api/flows/flow-2',
      '/api/flows/flow-1',
      '/api/weaviate/documents/document-2',
      '/api/weaviate/documents/document-1',
    ])
    assert.deepEqual(result.failures, [])
    assert.equal(result.removed.length, 8)
  })

  it('retries failures and records unexplained cleanup failures without skipping later resources', async () => {
    const attempts = new Map<string, number>()
    const result = await cleanupResources({
      api: {
        delete: async (path: string) => {
          attempts.set(path, (attempts.get(path) ?? 0) + 1)
          if (path.includes('bad')) throw new Error('cleanup denied')
          return null
        },
      },
      removeFileOutput: async () => undefined,
      resources: { loadedDocument: false, fileOutputs: [], chatSessionIds: [], flowIds: ['bad'], documentIds: ['good'] },
      retain: false,
      retryCount: 1,
      retryIntervalMs: 1,
    })
    assert.equal(attempts.get('/api/flows/bad'), 2)
    assert.equal(attempts.get('/api/weaviate/documents/good'), 1)
    assert.equal(result.failures.length, 1)
    assert.deepEqual(result.removed, ['document:good'])
  })

  it('retains all resources only when explicitly requested', async () => {
    const result = await cleanupResources({
      api: { delete: async () => { throw new Error('must not run') } },
      removeFileOutput: async () => { throw new Error('must not run') },
      resources: { loadedDocument: true, fileOutputs: [{ id: 'x', filename: 'x' }], chatSessionIds: ['s'], flowIds: ['f'], documentIds: ['d'] },
      retain: true,
      retryCount: 0,
      retryIntervalMs: 1,
    })
    assert.equal(result.retained, true)
    assert.deepEqual(result.attempted, [])
  })
})
