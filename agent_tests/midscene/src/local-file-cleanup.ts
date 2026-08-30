import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

import { z } from 'zod'

import type { SmokeConfig } from './config.js'

const execFileAsync = promisify(execFile)
const fileOutputSchema = z.object({ id: z.string().uuid(), filename: z.string().min(1).max(512) }).strict()
const cleanupResponseSchema = z.object({
  file_id: z.string().uuid(),
  database_absent: z.literal(true),
  storage_absent: z.literal(true),
}).passthrough()

export async function removeLocalFileOutput(
  config: SmokeConfig,
  rawFile: { id: string; filename: string },
): Promise<void> {
  const file = fileOutputSchema.parse(rawFile)
  if (!file.filename.startsWith(`${config.runPrefix}-`)) {
    throw new Error(`refusing to remove non-smoke file output ${file.id}`)
  }
  const result = await execFileAsync('docker', [
    'compose', 'exec', '-T', 'backend', 'python',
    '/app/scripts/testing/agent_ui_smoke_file_cleanup.py',
    '--file-id', file.id,
    '--run-prefix', config.runPrefix,
    '--filename', file.filename,
  ], {
    cwd: config.repoRoot,
    timeout: config.cleanupCommandTimeoutMs,
  })
  const response = cleanupResponseSchema.parse(JSON.parse(result.stdout.trim()))
  if (response.file_id !== file.id) throw new Error(`cleanup helper returned the wrong file ID for ${file.id}`)
}
