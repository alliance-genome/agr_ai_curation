import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ValidatorAttachmentStatus from './ValidatorAttachmentStatus'
import type { GenericProfileContract } from '@/services/genericProfileService'
const value: GenericProfileContract = { name: 'Genes', semantic_class: 'gene', fields: [{key: 'gene', value_schema: {kind:'object', fields:[{key:'symbol', value_schema:{kind:'string'}}, {key:'identifier', value_schema:{kind:'string'}}]}}], validator_mappings: [{mapping_id:'gene', capability_ref:{package_id:'example',package_version:'1',domain_pack_id:'example',domain_pack_version:'1',binding_id:'gene_validation'},capability_fingerprint:'sha256:test', inputs:{gene_symbol:{source:'field',field_path:'attributes.gene.symbol'}},outputs:{},policy:{unresolved:'requires_curator_review',blocks_readiness:false}}] }
describe('field validator attachment status', () => {
 it('distinguishes a parent from its validated part and unvalidated sibling', () => {
  const {rerender}=render(<ValidatorAttachmentStatus value={value} address={[0]}/>)
  expect(screen.getByText('No')).toBeInTheDocument()
  expect(screen.getByText('1 part has a validator')).toBeInTheDocument()
  rerender(<ValidatorAttachmentStatus value={value} address={[0,0]}/>)
  expect(screen.getByText('Yes · Gene validation')).toBeInTheDocument()
  rerender(<ValidatorAttachmentStatus value={value} address={[0,1]}/>)
  expect(screen.getByText('No')).toBeInTheDocument()
 })
 it('follows canonical keys after a rename or reorder and reports mapping problems', () => {
  const renamed=structuredClone(value)
  if(renamed.fields[0].value_schema.kind==='object') {renamed.fields[0].value_schema.fields.reverse();renamed.fields[0].value_schema.fields[1].display_name='New label'}
  render(<ValidatorAttachmentStatus value={renamed} address={[0,1]} issues={[{path:'validator_mappings[0].inputs.gene_symbol',code:'type',message:'Incompatible'}]}/>)
  expect(screen.getByText('Needs attention · Gene validation')).toBeInTheDocument()
 })
})

it('marks a saved attachment unavailable after a successful catalog refresh omits it', async () => {
  const { vi } = await import('vitest')
  const service = await import('@/services/genericProfileService')
  const catalog = await import('./ProfileValidatorCatalog')
  const spy = vi.spyOn(service, 'getProfileMappingOptions').mockResolvedValue({ fields: [], capabilities: [], next_cursor: null })
  try {
    render(<catalog.default value={value}><ValidatorAttachmentStatus value={value} address={[0,0]} /></catalog.default>)
    expect(await screen.findByText('Needs attention · Gene validation')).toBeInTheDocument()
  } finally { spy.mockRestore() }
})
