import { describe, expect, it } from 'vitest'
import { workshopProposalWarnings } from './workshopProposalWarnings'
import type { WorkshopAuthoringProposal } from '@/types/promptExplorer'
import type { GenericProfileContract } from '@/services/genericProfileService'

const fields: GenericProfileContract['fields'] = [
  { key: 'stock', display_name: 'Stock name', value_schema: { kind: 'string' } },
  { key: 'source', display_name: 'Source', value_schema: { kind: 'object', fields: [
    { key: 'number', display_name: 'Catalog number', value_schema: { kind: 'string' } },
  ] } },
]
const before: GenericProfileContract = { name: 'Stock', semantic_class: 'stock', fields }
const proposal = (after: GenericProfileContract | null) => ({
  diff: [{ path: 'custom_agent.output_contract', before: { profileContract: before } }],
  candidate: { draft_output: { profileContract: after } },
}) as unknown as WorkshopAuthoringProposal

describe('Workshop material change warnings', () => {
  it('identifies removed fields by identity, even when later fields shift position', () => {
    expect(workshopProposalWarnings(proposal({ ...before, fields: fields.slice(1) })))
      .toEqual(['Details or parts will be removed: Stock name.'])
  })
  it('warns for parts removed by a format conversion and for switching away from custom output', () => {
    expect(workshopProposalWarnings(proposal({ ...before, fields: [fields[0], { ...fields[1], value_schema: { kind: 'string' } }] })))
      .toEqual(['Details or parts will be removed: Catalog number.'])
    expect(workshopProposalWarnings(proposal(null))[0]).toContain('Stock name, Source, Catalog number')
  })
  it('does not label reordered or renamed details as removals', () => {
    expect(workshopProposalWarnings(proposal({ ...before, fields: [fields[1], { ...fields[0], display_name: 'Line name' }] })))
      .toEqual([])
  })
})
