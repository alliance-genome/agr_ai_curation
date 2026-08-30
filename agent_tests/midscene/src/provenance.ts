import { execFile } from 'node:child_process'
import { hostname } from 'node:os'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

export async function readRunnerProvenance(repoRoot: string, timeoutMs: number): Promise<{
  hostname: string
  git_sha: string
}> {
  const result = await execFileAsync('git', ['rev-parse', 'HEAD'], { cwd: repoRoot, timeout: timeoutMs })
  const gitSha = result.stdout.trim()
  if (!/^[0-9a-f]{40}$/.test(gitSha)) throw new Error('runner Git SHA is unavailable or malformed')
  return { hostname: hostname(), git_sha: gitSha }
}
