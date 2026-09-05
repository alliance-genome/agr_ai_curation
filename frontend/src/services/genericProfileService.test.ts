import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  archiveGenericProfile, createGenericProfile, GenericProfileApiError,
  listGenericProfiles, reviseGenericProfile, listProfileValidatorCapabilities,
  inspectProfileValidatorMappings, validateGenericProfile, type GenericProfileContract,
  compareGenericProfileRevision, listGenericProfileConsumers, getProfileMappingOptions,
} from './genericProfileService'

afterEach(() => vi.unstubAllGlobals())

const contract: GenericProfileContract = {
  name: 'Example', semantic_class: 'example', fields: [
    { key: 'sources', required: true, nullable: true, value_schema: {
      kind: 'array', items: { kind: 'object', fields: [
        { key: 'identifier', source_labels: ['Paper ID'], value_schema: { kind: 'string' } },
      ] },
    } },
  ],
}

describe('generic profile API', () => {
  it('inspects compatible slots for a typed unsaved contract without creating a profile', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    await getProfileMappingOptions(contract, 'cap/next')
    expect(fetch).toHaveBeenCalledWith('/api/agent-studio/generic-profiles/validator-options?after=cap%2Fnext', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(contract),
    })
  })
  it('reads saved consumers with an encoded opaque cursor and no mutation body', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    await listGenericProfileConsumers('profile/id', 'flow/id/node')
    expect(fetch).toHaveBeenCalledWith('/api/agent-studio/generic-profiles/profile%2Fid/consumers?after=flow%2Fid%2Fnode', undefined)
  })
  it('compares a complete draft with an exact encoded revision endpoint', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    await compareGenericProfileRevision('profile/id', 2, contract)
    expect(fetch).toHaveBeenCalledWith('/api/agent-studio/generic-profiles/profile%2Fid/revisions/2/compare', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(contract),
    })
  })
  it('uses typed capability and revision inspection endpoints with opaque cursors', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    await listProfileValidatorCapabilities('{"binding":"id/a"}')
    await inspectProfileValidatorMappings('profile/id', 3)
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/agent-studio/generic-profiles/validator-capabilities?after=%7B%22binding%22%3A%22id%2Fa%22%7D',
      '/api/agent-studio/generic-profiles/profile%2Fid/revisions/3/validator-mappings',
    ])
  })

  it('preserves authoritative mapping errors from validation without persistence', async () => {
    const detail = { code: 'profile_mapping_invalid', issues: [{ path: 'validator_mappings[0]', code: 'unavailable', message: 'Unavailable' }] }
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 422 }))
    vi.stubGlobal('fetch', fetch)
    await expect(validateGenericProfile(contract)).rejects.toMatchObject({ status: 422, detail })
    expect(fetch.mock.calls[0][0]).toBe('/api/agent-studio/generic-profiles/validate')
    expect(fetch).toHaveBeenCalledOnce()
  })
  it('sends the complete typed contract and an explicit stale-save guard', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 2 })))
    vi.stubGlobal('fetch', fetch)
    await reviseGenericProfile('profile-id', 1, contract)
    expect(fetch).toHaveBeenCalledWith('/api/agent-studio/generic-profiles/profile-id/revisions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision: 1, contract }),
    })
  })

  it('preserves field-addressed errors for the editor without changing its draft', async () => {
    const detail = [{ loc: ['body', 'contract', 'fields', 0, 'key'], msg: 'Reserved key' }]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 422 })))
    const original = JSON.stringify(contract)
    await expect(createGenericProfile(contract)).rejects.toMatchObject({ status: 422, detail })
    expect(JSON.stringify(contract)).toBe(original)
  })

  it('keeps conflicts distinct from validation failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Stale revision' }), { status: 409 })))
    await expect(archiveGenericProfile('profile-id', 1)).rejects.toBeInstanceOf(GenericProfileApiError)
  })

  it('uses the server keyset cursor without loading hidden pages automatically', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ profiles: [], next_cursor: null })))
    vi.stubGlobal('fetch', fetch)
    await listGenericProfiles('cursor/id')
    expect(fetch).toHaveBeenCalledOnce()
    expect(fetch).toHaveBeenCalledWith('/api/agent-studio/generic-profiles?after_id=cursor%2Fid', undefined)
  })
})
