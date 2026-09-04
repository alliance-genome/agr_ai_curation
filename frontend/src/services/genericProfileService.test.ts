import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  archiveGenericProfile, createGenericProfile, GenericProfileApiError,
  listGenericProfiles, reviseGenericProfile, type GenericProfileContract,
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
