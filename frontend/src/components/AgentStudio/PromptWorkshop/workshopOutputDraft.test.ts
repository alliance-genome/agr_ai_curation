import { describe, expect, it } from 'vitest'
import { GenericProfileApiError, type GenericProfileContract, type GenericProfileRevision } from '@/services/genericProfileService'
import { emptyOutputDraft, hydrateProfileOutput, outputDraftEqual, outputDraftFromContract, outputDraftSavePayload, profileValidationIssues } from './workshopOutputDraft'

const contract: GenericProfileContract = { name: 'Records', semantic_class: 'record', fields: [{ key: 'title', value_schema: { kind: 'string' } }] }
const revision: GenericProfileRevision = { id: 'revision-id', profile_id: 'profile-id', revision: 2, fingerprint: 'sha256:fixture', contract, creator_id: 1, created_at: '2026-09-05T00:00:00Z' }
const pin = { profile_id: revision.profile_id, profile_revision_id: revision.id, revision: revision.revision, fingerprint: revision.fingerprint }

describe('explicit Workshop output draft', () => {
  it('round-trips an exact packaged builder without assigning a model schema', () => {
    const ref = { package_id: 'fixture.package', agent_id: 'builder', domain_pack_id: 'fixture.domain' }
    const draft = outputDraftFromContract({ output_state: 'structured_extraction', output_mode: 'domain', domain_extraction_ref: ref })
    expect(draft.schemaKey).toBe('')
    expect(draft.domainExtractionRef).toEqual(ref)
    expect(outputDraftSavePayload(draft, null)).toEqual({ output_contract: {
      output_state: 'structured_extraction', output_mode: 'domain', output_schema_key: null, domain_extraction_ref: ref,
    } })
    expect(outputDraftEqual(draft, { ...draft, domainExtractionRef: { ...ref, agent_id: 'other' } })).toBe(false)
    expect(() => outputDraftSavePayload({ ...draft, schemaKey: 'Envelope' }, null)).toThrow('not both')
  })

  it('never infers flexible generic from an empty schema', () => {
    expect(emptyOutputDraft().mode).toBe('none')
    expect(outputDraftFromContract({ output_state: 'none' }).mode).toBe('none')
    expect(outputDraftFromContract({ output_state: 'structured_extraction', output_mode: 'unprofiled_generic' }).mode).toBe('unprofiled_generic')
    expect(() => outputDraftSavePayload(emptyOutputDraft('domain'), null)).toThrow('Choose a domain envelope')
  })

  it('requires exact profile identity on load and preserves its pin on unchanged save', () => {
    const initial = outputDraftFromContract({ output_state: 'structured_extraction', output_mode: 'profile_bound_generic', generic_profile_ref: pin })
    expect(() => outputDraftSavePayload(initial, contract)).toThrow('Load the saved Output Structure')
    expect(() => hydrateProfileOutput(initial, { ...revision, revision: 3 })).toThrow('does not match')
    const loaded = hydrateProfileOutput(initial, revision)
    expect(outputDraftSavePayload(loaded, contract)).toEqual({ output_contract: {
      output_state: 'structured_extraction', output_mode: 'profile_bound_generic', generic_profile_ref: pin,
    } })
    expect(loaded.profileContract).not.toBe(contract)
  })

  it('submits the changed full contract and does not mutate the existing revision', () => {
    const draft = { ...emptyOutputDraft('profile_bound_generic'), profilePin: pin, profileContract: { ...contract, name: 'Changed' } }
    expect(outputDraftSavePayload(draft, contract)).toEqual({ new_generic_profile: draft.profileContract })
    expect(outputDraftSavePayload(draft, contract, true)).toEqual({ revise_generic_profile: { base: pin, contract: draft.profileContract } })
    expect(contract.name).toBe('Records')
    expect(outputDraftEqual(draft, { ...draft, mode: 'none' })).toBe(false)
    expect(outputDraftEqual(draft, structuredClone(draft))).toBe(true)
  })

  it('emits explicit domain, none and flexible generic transitions', () => {
    const domain = outputDraftFromContract({ output_state: 'structured_extraction', output_mode: 'domain', output_schema_key: 'ExampleEnvelope' })
    expect(outputDraftSavePayload(domain, null).output_contract).toMatchObject({ output_mode: 'domain', output_schema_key: 'ExampleEnvelope' })
    expect(outputDraftSavePayload(emptyOutputDraft(), null)).toEqual({ output_contract: { output_state: 'none' } })
    expect(outputDraftSavePayload(emptyOutputDraft('unprofiled_generic'), null).output_contract).toMatchObject({ output_mode: 'unprofiled_generic' })
  })

  it('projects authoritative nested and mapping errors to field-linked messages', () => {
    expect(profileValidationIssues(new GenericProfileApiError(422, [{ loc: ['body', 'fields', 0, 'value_schema', 'array', 'items', 'enum', 'values'], type: 'value_error', msg: 'Choices must be unique' }]))).toEqual([
      { path: 'fields[0].value_schema.items.values', code: 'value_error', message: 'Choices must be unique' },
    ])
    const issue = { path: 'validator_mappings[0].inputs', code: 'provider_scope', message: 'Provider input is required' }
    expect(profileValidationIssues(new GenericProfileApiError(422, { issues: [issue] }))).toEqual([issue])
    expect(profileValidationIssues(new GenericProfileApiError(409, 'Stale revision'))[0].code).toBe('conflict')
  })
})
