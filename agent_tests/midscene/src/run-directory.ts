import { mkdir } from 'node:fs/promises'

export async function createFreshRunDirectory(outputRoot: string, runDir: string, runId: string): Promise<void> {
  await mkdir(outputRoot, { recursive: true })
  try {
    await mkdir(runDir)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'EEXIST') {
      throw new Error(`run ID ${runId} already has an evidence directory`)
    }
    throw error
  }
}
