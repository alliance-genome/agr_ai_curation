import type { WorkshopAuthoringProposal } from '@/types/promptExplorer'
import type { WorkshopOutputDraft } from './PromptWorkshop/workshopOutputDraft'
import { profileFieldRows } from './PromptWorkshop/profileEditorModel'

/** Material losses stay visible even when the model's summary omits them. */
export function workshopProposalWarnings(proposal: WorkshopAuthoringProposal): string[] {
  const outputChange = proposal.diff.find(entry => entry.path === 'custom_agent.output_contract')
  const before = (outputChange?.before as WorkshopOutputDraft | undefined)?.profileContract
  if (!before) return []
  const after = proposal.candidate.draft_output?.profileContract
  const nextFields = new Set(after ? profileFieldRows(after).map(row => row.canonicalPath) : [])
  const removed = profileFieldRows(before).filter(row => !nextFields.has(row.canonicalPath))
  const warnings: string[] = []
  if (removed.length) warnings.push(`Details or parts will be removed: ${removed.map(row => row.field.display_name || row.field.key).join(', ')}.`)
  const changedValidation = (before.validator_mappings ?? []).some(mapping => {
    const next = after?.validator_mappings?.find(candidate => candidate.mapping_id === mapping.mapping_id)
    return !next || (mapping.policy.blocks_readiness && !next.policy.blocks_readiness)
      || (mapping.policy.unresolved !== 'informational' && next.policy.unresolved === 'informational')
  })
  if (changedValidation) warnings.push('Some validation will be removed or made optional. Review the validator changes before applying.')
  return warnings
}
