import { CASE_NAMES, type CaseName } from './config.js'

export interface RunResultLike {
  cases: readonly { sourcePath: string; status: string }[]
}

export function canonicalCaseStatuses(result: RunResultLike | undefined): Record<CaseName, string> {
  return Object.fromEntries(CASE_NAMES.map((caseName) => {
    const suffix = `cases/${caseName}.yaml`
    const matches = result?.cases.filter((item) => item.sourcePath.endsWith(suffix)) ?? []
    return [caseName, matches.length === 1 ? matches[0]!.status : 'not-run']
  })) as Record<CaseName, string>
}

export function executedCanonicalCases(result: RunResultLike | undefined): CaseName[] {
  if (!result) return []
  return CASE_NAMES.filter((caseName) => {
    const suffix = `cases/${caseName}.yaml`
    return result.cases.some((item) => item.sourcePath.endsWith(suffix))
  })
}
