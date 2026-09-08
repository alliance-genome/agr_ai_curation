import type { CurationCandidate, CurationDraftField } from '@/features/curation/types'
import { resolveEnvelopeFieldPath } from '@/features/curation/workspace/workspaceState'

// Packages may narrow the decision surface through workspace_display. Without
// a declaration every projected field remains visible, including new packages.
export function isHorizontalGridDecisionField(
  candidate: CurationCandidate,
  field: CurationDraftField,
): boolean {
  const rowMetadata = candidate.metadata.review_row_metadata
  if (rowMetadata === undefined || rowMetadata === null) {
    return true
  }
  if (typeof rowMetadata !== 'object' || Array.isArray(rowMetadata)) {
    throw new Error('review_row_metadata must be an object')
  }
  const display = (rowMetadata as Record<string, unknown>).workspace_display
  if (display === undefined || display === null) {
    return true
  }
  if (typeof display !== 'object' || Array.isArray(display)) {
    throw new Error('workspace_display must be an object')
  }
  const policy: unknown = (display as Record<string, unknown>).review_policy
  if (policy === undefined || policy === null) {
    return true
  }
  if (typeof policy !== 'object' || Array.isArray(policy)) {
    throw new Error('workspace_display.review_policy must be an object')
  }
  const declaration = policy as Record<string, unknown>
  if (declaration.mode === 'all') {
    return true
  }
  if (declaration.mode !== 'groups' && declaration.mode !== 'fields') {
    throw new Error('workspace_display.review_policy.mode must be all, groups, or fields')
  }
  const key = declaration.mode === 'groups' ? 'decision_groups' : 'decision_fields'
  const selection = declaration[key]
  if (!Array.isArray(selection) || !selection.every((value) => typeof value === 'string' && value.trim())) {
    throw new Error(`workspace_display.review_policy.${key} must be an array of non-empty strings`)
  }
  return declaration.mode === 'groups'
    ? field.group_key !== null && selection.includes(field.group_key)
    : selection.includes(resolveEnvelopeFieldPath(field))
}
