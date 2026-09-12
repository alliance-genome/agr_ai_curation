import type { AgentOutputContract, DomainExtractionRef, GenericProfilePin } from '@/types/agentExecution'
import type { GenericProfileContract, GenericProfileRevision, ProfileMappingDiagnostic } from '@/services/genericProfileService'
import { GenericProfileApiError } from '@/services/genericProfileService'
import { canonicalAuthoringJson } from '../authoringContext'

/** Incomplete local authoring state is intentionally distinct from an executable pin. */
export interface WorkshopOutputDraft {
  mode: 'none' | 'domain' | 'profile_bound_generic' | 'unprofiled_generic'
  schemaKey: string
  domainExtractionRef?: DomainExtractionRef
  profilePin: GenericProfilePin | null
  profileContract: GenericProfileContract | null
}

export function emptyOutputDraft(mode: WorkshopOutputDraft['mode'] = 'none'): WorkshopOutputDraft {
  return {
    mode, schemaKey: '', profilePin: null,
    profileContract: mode === 'profile_bound_generic'
      ? { name: '', description: '', semantic_class: '', fields: [], validator_mappings: [] } : null,
  }
}

/** Call only with the exact saved executable revision, never a template guess. */
export function outputDraftFromContract(contract: AgentOutputContract): WorkshopOutputDraft {
  if (contract.output_state === 'none') return emptyOutputDraft()
  if (contract.output_mode === 'domain') return {
    ...emptyOutputDraft('domain'), schemaKey: contract.output_schema_key ?? '',
    ...(contract.domain_extraction_ref ? { domainExtractionRef: structuredClone(contract.domain_extraction_ref) } : {}),
  }
  if (contract.output_mode === 'unprofiled_generic') return emptyOutputDraft('unprofiled_generic')
  return { ...emptyOutputDraft('profile_bound_generic'), profilePin: structuredClone(contract.generic_profile_ref), profileContract: null }
}

export function hydrateProfileOutput(draft: WorkshopOutputDraft, revision: GenericProfileRevision): WorkshopOutputDraft {
  const pin = draft.profilePin
  if (draft.mode !== 'profile_bound_generic' || !pin || pin.profile_id !== revision.profile_id
      || pin.profile_revision_id !== revision.id || pin.revision !== revision.revision || pin.fingerprint !== revision.fingerprint) {
    throw new Error('The loaded profile does not match this saved executable revision.')
  }
  return { ...draft, profileContract: structuredClone(revision.contract) }
}

export function outputDraftEqual(left: WorkshopOutputDraft, right: WorkshopOutputDraft): boolean {
  return canonicalAuthoringJson(left) === canonicalAuthoringJson(right)
}

/** Only explicit Save turns a draft into a persisted output transition. */
export function outputDraftSavePayload(draft: WorkshopOutputDraft, savedProfile: GenericProfileContract | null, reviseExisting = false): {
  output_contract?: AgentOutputContract
  new_generic_profile?: GenericProfileContract
  revise_generic_profile?: { base: GenericProfilePin; contract: GenericProfileContract }
} {
  if (draft.mode === 'none') return { output_contract: { output_state: 'none' } }
  if (draft.mode === 'domain') {
    if (draft.domainExtractionRef) {
      if (draft.schemaKey) throw new Error('Choose either a packaged builder format or a model-response schema, not both.')
      return { output_contract: {
        output_state: 'structured_extraction', output_mode: 'domain', output_schema_key: null,
        domain_extraction_ref: structuredClone(draft.domainExtractionRef),
      } }
    }
    if (!draft.schemaKey) throw new Error('Choose a domain envelope before saving.')
    return { output_contract: { output_state: 'structured_extraction', output_mode: 'domain', output_schema_key: draft.schemaKey } }
  }
  if (draft.mode === 'unprofiled_generic') return {
    output_contract: { output_state: 'structured_extraction', output_mode: 'unprofiled_generic' },
  }
  if (!draft.profileContract) throw new Error('Load the saved Output Structure before saving.')
  if (draft.profilePin && savedProfile && canonicalAuthoringJson(draft.profileContract) === canonicalAuthoringJson(savedProfile)) {
    return { output_contract: {
      output_state: 'structured_extraction', output_mode: 'profile_bound_generic', generic_profile_ref: structuredClone(draft.profilePin),
    } }
  }
  if (draft.profilePin && reviseExisting) return {
    revise_generic_profile: { base: structuredClone(draft.profilePin), contract: structuredClone(draft.profileContract) },
  }
  return { new_generic_profile: structuredClone(draft.profileContract) }
}

/** Keep backend conformance/mapping diagnostics authoritative; preserve invalid input. */
export function profileValidationIssues(error: unknown): ProfileMappingDiagnostic[] {
  if (!(error instanceof GenericProfileApiError)) return [{ path: '', code: 'request_failed', message: error instanceof Error ? error.message : 'Could not check the structure. Please retry.' }]
  const detail = error.detail
  if (Array.isArray(detail)) return detail.map((item) => {
    const location: unknown[] = Array.isArray(item.loc) ? item.loc : []
    const path = location.filter((part) => !['body', 'contract', 'object', 'array', 'enum', 'string', 'integer', 'number', 'boolean'].includes(String(part)))
      .reduce<string>((result, part) => typeof part === 'number' ? `${result}[${part}]` : result ? `${result}.${part}` : String(part), '')
    return { path, code: String(item.type ?? 'invalid'), message: String(item.msg ?? 'Check this field.') }
  })
  if (detail && typeof detail === 'object' && 'issues' in detail && Array.isArray(detail.issues)) {
    return detail.issues.map((issue) => ({ path: String(issue.path ?? ''), code: String(issue.code ?? 'invalid'), message: String(issue.message ?? 'Check this field.') }))
  }
  return [{ path: '', code: error.status === 409 ? 'conflict' : 'invalid', message: error.message }]
}
