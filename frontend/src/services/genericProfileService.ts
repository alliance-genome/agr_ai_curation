/** Closed generic profiles are curator-owned contracts, not submission schemas. */

export type GenericProfileValueSchema =
  | { kind: 'string' | 'integer' | 'number' | 'boolean' }
  | { kind: 'enum'; values: string[] }
  | { kind: 'object'; fields: GenericProfileField[] }
  | { kind: 'array'; items: GenericProfileValueSchema }

export interface GenericProfileField {
  key: string
  display_name?: string
  description?: string
  required?: boolean
  nullable?: boolean
  source_labels?: string[]
  value_schema: GenericProfileValueSchema
}

export interface GenericProfileContract {
  contract_version?: 1
  name: string
  description?: string
  semantic_class: string
  fields: GenericProfileField[]
  validator_mappings?: ProfileValidatorMapping[]
}

export interface ValidatorCapabilityRef {
  package_id: string
  package_version: string
  domain_pack_id: string
  domain_pack_version: string
  binding_id: string
}

export type ProfileMappingInput =
  | { source?: 'field'; field_path: string; value?: null }
  | { source: 'constant'; value: unknown; field_path?: null }
  | { source: 'context'; field_path?: null; value?: null }

export type ProfileUnresolvedPolicy = 'informational' | 'requires_curator_review' | 'error'

export interface ProfileValidatorMapping {
  mapping_id: string
  capability_ref: ValidatorCapabilityRef
  capability_fingerprint: string
  inputs: Record<string, ProfileMappingInput>
  outputs: Record<string, string>
  policy: { unresolved: ProfileUnresolvedPolicy; blocks_readiness: boolean }
  mode?: 'whole' | 'per_element'
}

export interface ReusableValidatorInput {
  value_schema: GenericProfileValueSchema
  nullable: boolean
  required: boolean
  allow_field: boolean
  allow_constant: boolean
  /** Display-only, package-owned selector; curators cannot replace its path. */
  context_selector: Record<string, unknown> | null
}

export interface ReusableValidatorOutput {
  value_schema: GenericProfileValueSchema
  nullable: boolean
  result_path: string
}

export interface CustomProfileValidatorReuse {
  enabled: boolean
  inputs: Record<string, ReusableValidatorInput>
  required_any_inputs: string[][]
  outputs: Record<string, ReusableValidatorOutput>
  policy: {
    unresolved_default: ProfileUnresolvedPolicy
    unresolved_allowed: ProfileUnresolvedPolicy[]
    readiness_default: boolean
    readiness_allowed: boolean[]
  }
  supports_whole_array: boolean
  supports_element_fanout: boolean
  requires_evidence: boolean
  provider_input_slots: Record<string, string>
}

export interface ProfileValidatorCapability {
  capability_ref: ValidatorCapabilityRef
  fingerprint: string
  state: 'active' | 'under_development'
  selectable: boolean
  diagnostics: string[]
  metadata: {
    validator_binding_id: string
    display_name?: string
    reason?: string
    custom_profile_reuse: CustomProfileValidatorReuse
    group_scope?: {
      required_any_active_group: string[]
      provider_value_field_paths: string[]
      allowed_provider_values: string[]
      allow_cross_provider: boolean
    }
  }
}

export interface ProfileMappingDiagnostic {
  path: string
  code: string
  message: string
}

export interface ProfileMappingFieldOption {
  path: string
  display_name: string
  value_schema: GenericProfileValueSchema
  required: boolean
  nullable: boolean
  array_domains: string[]
}

export interface ProfileValidatorOptions extends ProfileValidatorCapability {
  input_paths: Record<string, string[]>
  output_paths: Record<string, string[]>
}

export interface ProfileMappingOptions {
  fields: ProfileMappingFieldOption[]
  capabilities: ProfileValidatorOptions[]
  next_cursor: string | null
}

export interface ProfileMappingInspection {
  profile_revision_id: string
  fingerprint: string
  validator_mappings: ProfileValidatorMapping[]
  diagnostics: ProfileMappingDiagnostic[]
  capability_snapshots: Record<string, unknown>[]
  state: 'unmapped' | 'unsupported' | 'compatible'
  semantic_execution: 'not_executed'
  submission_readiness: 'not_asserted'
}

export interface GenericProfileSummary {
  id: string
  owner_id: number
  project_id: string | null
  visibility: 'private' | 'project'
  name: string
  description: string
  semantic_class: string
  head_revision: number
  archived: boolean
  created_at: string
  updated_at: string
}

export interface GenericProfileRevision {
  id: string
  profile_id: string
  revision: number
  fingerprint: string
  contract: GenericProfileContract
  creator_id: number
  created_at: string
}

export interface ProfileCompatibilityFinding {
  path: string
  code: string
  breaking: boolean
  before: unknown
  after: unknown
}

export interface GenericProfileDetail {
  can_edit: boolean
  profile: GenericProfileSummary
  revision: GenericProfileRevision
  compatibility: ProfileCompatibilityFinding[]
}

export interface ProfileRevisionComparison {
  base_revision: GenericProfileRevision
  proposed_fingerprint: string
  compatibility: ProfileCompatibilityFinding[]
}

export class GenericProfileApiError extends Error {
  constructor(readonly status: number, readonly detail: unknown) {
    super(typeof detail === 'string' ? detail : 'Check the highlighted profile fields and try again.')
    this.name = 'GenericProfileApiError'
  }
}

const BASE = '/api/agent-studio/generic-profiles'

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(BASE + path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const detail = payload !== null && typeof payload === 'object' && 'detail' in payload
      ? payload.detail : 'Profile request failed. Please try again.'
    throw new GenericProfileApiError(response.status, detail)
  }
  return response.json()
}

export const listGenericProfiles = (afterId?: string) =>
  request<{ profiles: GenericProfileSummary[]; next_cursor: string | null }>(
    afterId ? '?after_id=' + encodeURIComponent(afterId) : '',
  )

export const getGenericProfile = (id: string) =>
  request<GenericProfileDetail>('/' + encodeURIComponent(id))

export interface ProfileConsumer {
  key: string
  kind: 'agent' | 'flow'
  name: string
  agent_id: string
  agent_revision_id: string
  agent_revision: number
  profile_revision: number
  is_current_agent_revision: boolean
  archived: boolean
  flow_id: string | null
  node_id: string | null
}

export interface ProfileConsumerPage {
  consumers: ProfileConsumer[]
  next_cursor: string | null
  head_revision: number
}

export const listGenericProfileConsumers = (id: string, after?: string) =>
  request<ProfileConsumerPage>('/' + encodeURIComponent(id) + '/consumers' +
    (after === undefined ? '' : '?after=' + encodeURIComponent(after)))

export const getGenericProfileRevision = (id: string, revision: number) =>
  request<GenericProfileRevision>('/' + encodeURIComponent(id) + '/revisions/' + revision)

export const compareGenericProfileRevision = (id: string, revision: number, contract: GenericProfileContract) =>
  request<ProfileRevisionComparison>('/' + encodeURIComponent(id) + '/revisions/' + revision + '/compare', contract)

export const listGenericProfileRevisions = (id: string, beforeRevision?: number) =>
  request<{ revisions: GenericProfileRevision[]; next_cursor: number | null }>(
    '/' + encodeURIComponent(id) + '/revisions' +
    (beforeRevision === undefined ? '' : '?before_revision=' + beforeRevision),
  )

export const validateGenericProfile = (contract: GenericProfileContract) =>
  request<{ contract: GenericProfileContract; fingerprint: string }>('/validate', contract)

export const listProfileValidatorCapabilities = (after?: string) =>
  request<{ capabilities: ProfileValidatorCapability[]; next_cursor: string | null }>(
    '/validator-capabilities' + (after === undefined ? '' : '?after=' + encodeURIComponent(after)),
  )

export const getProfileMappingOptions = (contract: GenericProfileContract, after?: string) =>
  request<ProfileMappingOptions>('/validator-options' +
    (after === undefined ? '' : '?after=' + encodeURIComponent(after)), contract)

export const inspectProfileValidatorMappings = (id: string, revision: number) =>
  request<ProfileMappingInspection>('/' + encodeURIComponent(id) + '/revisions/' + revision + '/validator-mappings')

export const createGenericProfile = (
  contract: GenericProfileContract,
  sharing: { visibility: 'private' | 'project'; project_id?: string | null } = { visibility: 'private' },
) => request<GenericProfileDetail>('', { contract, ...sharing })

export const reviseGenericProfile = (id: string, expectedRevision: number, contract: GenericProfileContract) =>
  request<GenericProfileDetail>('/' + encodeURIComponent(id) + '/revisions', {
    expected_revision: expectedRevision, contract,
  })

export const cloneGenericProfile = (id: string, revision: number, name: string) =>
  request<GenericProfileDetail>('/' + encodeURIComponent(id) + '/clone', { revision, name })

export const archiveGenericProfile = (id: string, expectedRevision: number) =>
  request<GenericProfileSummary>('/' + encodeURIComponent(id) + '/archive', { expected_revision: expectedRevision })
