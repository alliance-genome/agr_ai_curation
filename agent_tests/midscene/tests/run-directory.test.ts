import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { describe, it } from 'node:test'

import { createFreshRunDirectory } from '../src/run-directory.js'

describe('run evidence directory', () => {
  it('creates a new run directory and rejects reuse of the same run ID', async () => {
    const outputRoot = await mkdtemp(path.join(os.tmpdir(), 'agent-ui-smoke-output-'))
    const runId = 'one-run'
    const runDir = path.join(outputRoot, runId)
    try {
      await createFreshRunDirectory(outputRoot, runDir, runId)
      await assert.rejects(
        createFreshRunDirectory(outputRoot, runDir, runId),
        /run ID one-run already has an evidence directory/,
      )
    } finally {
      await rm(outputRoot, { recursive: true, force: true })
    }
  })
})
