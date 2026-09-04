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
  // ALL-1037 replaces this empty extension with its typed mapping contract.
  validator_mappings?: never[]
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
  profile: GenericProfileSummary
  revision: GenericProfileRevision
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

export const getGenericProfileRevision = (id: string, revision: number) =>
  request<GenericProfileRevision>('/' + encodeURIComponent(id) + '/revisions/' + revision)

export const listGenericProfileRevisions = (id: string, beforeRevision?: number) =>
  request<{ revisions: GenericProfileRevision[]; next_cursor: number | null }>(
    '/' + encodeURIComponent(id) + '/revisions' +
    (beforeRevision === undefined ? '' : '?before_revision=' + beforeRevision),
  )

export const validateGenericProfile = (contract: GenericProfileContract) =>
  request<{ contract: GenericProfileContract; fingerprint: string }>('/validate', contract)

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
