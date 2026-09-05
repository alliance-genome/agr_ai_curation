import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfileValidatorEditor from './ProfileValidatorEditor'
import type { GenericProfileContract, ProfileMappingOptions, ProfileValidatorOptions } from '@/services/genericProfileService'

const api = vi.hoisted(() => ({ getProfileMappingOptions: vi.fn() }))
vi.mock('@/services/genericProfileService', () => api)
const contract: GenericProfileContract = { name: 'Records', semantic_class: 'record', fields: [
  { key: 'paper_name', display_name: 'Paper name', required: true, value_schema: { kind: 'string' } },
  { key: 'resolved_id', nullable: true, value_schema: { kind: 'string' } },
] }
const cap: ProfileValidatorOptions = {
  capability_ref: { package_id: 'example', package_version: '1', domain_pack_id: 'record', domain_pack_version: '1', binding_id: 'lookup' },
  fingerprint: 'sha256:exact', state: 'active', selectable: true, diagnostics: [],
  input_paths: { mention: ['attributes.paper_name'], provider: [], context: [] },
  output_paths: { identifier: ['attributes.resolved_id'] },
  metadata: { validator_binding_id: 'lookup', display_name: 'Identifier lookup',
    group_scope: { required_any_active_group: [], allowed_provider_values: ['EX'], allow_cross_provider: false, provider_value_field_paths: ['provider'] },
    custom_profile_reuse: { enabled: true, inputs: {
      mention: { value_schema: { kind: 'string' }, required: true, nullable: false, allow_field: true, allow_constant: false, context_selector: null },
      provider: { value_schema: { kind: 'enum', values: ['EX'] }, required: true, nullable: false, allow_field: false, allow_constant: true, context_selector: null },
      context: { value_schema: { kind: 'string' }, required: false, nullable: true, allow_field: false, allow_constant: false, context_selector: { source: 'record', path: 'document_id' } },
    }, outputs: { identifier: { value_schema: { kind: 'string' }, nullable: true, result_path: 'identifier' } },
    policy: { unresolved_default: 'requires_curator_review', unresolved_allowed: ['informational', 'requires_curator_review'], readiness_default: false, readiness_allowed: [false] },
    required_any_inputs: [], supports_whole_array: false, supports_element_fanout: true, requires_evidence: true, provider_input_slots: { provider: 'provider' },
  } },
}
const response: ProfileMappingOptions = { fields: contract.fields.map((field) => ({ path: `attributes.${field.key}`, display_name: field.display_name || field.key,
  value_schema: field.value_schema, required: field.required ?? false, nullable: field.nullable ?? false, array_domains: [] })), capabilities: [cap], next_cursor: null }

function choose(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }))
  fireEvent.click(screen.getByRole('option', { name: option }))
}
function Harness({ changed = vi.fn(), validate = vi.fn() }) {
  const [value, setValue] = useState(contract)
  return <ProfileValidatorEditor value={value} onChange={(next) => { changed(next); setValue(next) }} onValidate={validate} issues={[]} />
}
async function loadAndAdd() {
  fireEvent.click(screen.getByRole('button', { name: 'Find compatible validators' }))
  await screen.findByRole('combobox', { name: 'Find validators for canonical field' })
  choose('Find validators for canonical field', 'Paper name · attributes.paper_name')
  fireEvent.click(screen.getByRole('button', { name: 'Map field to mention · Identifier lookup' }))
}

describe('typed semantic validator controls', () => {
  beforeEach(() => api.getProfileMappingOptions.mockReset().mockResolvedValue(response))

  it('maps explicit field/context/typed constant/output/policy choices into the one draft', async () => {
    const changed = vi.fn(), validate = vi.fn()
    render(<Harness changed={changed} validate={validate} />)
    expect(api.getProfileMappingOptions).not.toHaveBeenCalled()
    await loadAndAdd()
    choose('Input source · provider · validator_1', 'Fixed typed value')
    choose('Fixed value · provider · validator_1', 'EX')
    choose('Input source · context · validator_1', 'Package-owned record context')
    choose('Write identifier to · validator_1', 'attributes.resolved_id')
    choose('If unresolved · validator_1', 'Informational finding')
    const draft = changed.mock.lastCall![0]
    expect(draft.validator_mappings).toEqual([{
      mapping_id: 'validator_1', capability_ref: cap.capability_ref, capability_fingerprint: cap.fingerprint,
      inputs: { mention: { source: 'field', field_path: 'attributes.paper_name' }, provider: { source: 'constant', value: 'EX' }, context: { source: 'context' } },
      outputs: { identifier: 'attributes.resolved_id' }, mode: 'whole', policy: { unresolved: 'informational', blocks_readiness: false },
    }])
    expect(screen.getByText(/context selector cannot be edited/)).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    fireEvent.blur(screen.getByRole('combobox', { name: 'Input field · mention · validator_1' }))
    expect(validate).toHaveBeenCalled()
    expect(api.getProfileMappingOptions).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'Remove mapping validator_1' }))
    expect(changed.mock.lastCall![0].validator_mappings).toEqual([])
  })

  it('shows development and honest unmapped states without offering arbitrary agents', async () => {
    api.getProfileMappingOptions.mockResolvedValue({ ...response, capabilities: [{ ...cap, state: 'under_development', selectable: false, diagnostics: ['Implementation under development'] }] })
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Find compatible validators' }))
    await screen.findByRole('combobox', { name: 'Find validators for canonical field' })
    choose('Find validators for canonical field', 'Paper name · attributes.paper_name')
    expect(screen.getByRole('button', { name: 'Map field to mention · Identifier lookup' })).toBeDisabled()
    expect(screen.getByText(/No available compatible/)).toBeInTheDocument()
    expect(screen.getByText(/No semantic validators mapped/)).toBeInTheDocument()
  })

  it('invalidates field choices after structure changes but preserves mappings and input', async () => {
    const changed = vi.fn()
    const view = render(<ProfileValidatorEditor value={contract} onChange={changed} onValidate={vi.fn()} issues={[]} />)
    await loadAndAdd()
    const draft: GenericProfileContract = changed.mock.lastCall![0]
    view.rerender(<ProfileValidatorEditor value={{ ...draft, fields: [] }} onChange={changed} onValidate={vi.fn()}
      issues={[{ path: 'validator_mappings[0].inputs.mention.field_path', code: 'path', message: 'Field is no longer declared' }]} />)
    expect(screen.getByText(/Fields changed/)).toBeInTheDocument()
    expect(screen.getByText('Field is no longer declared')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Map field to mention · Identifier lookup' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove mapping validator_1' })).toBeEnabled()
    expect(changed).toHaveBeenCalledOnce()
  })

  it('retains loaded capabilities on a failed next-page request and retries the cursor', async () => {
    api.getProfileMappingOptions.mockResolvedValueOnce({ ...response, next_cursor: 'next' }).mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValueOnce({ ...response, capabilities: [] })
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Find compatible validators' }))
    await screen.findByRole('button', { name: 'Load more compatible validators' })
    fireEvent.click(screen.getByRole('button', { name: 'Load more compatible validators' }))
    await screen.findByText(/Unavailable Check the structure/)
    fireEvent.click(screen.getByRole('button', { name: 'Load more compatible validators' }))
    await waitFor(() => expect(api.getProfileMappingOptions).toHaveBeenCalledTimes(3))
    expect(api.getProfileMappingOptions.mock.calls.slice(1).map((call) => call[1])).toEqual(['next', 'next'])
  })

  it('offers per-element destinations only within the selected shared array domain', async () => {
    const fields = ['records', 'other'].flatMap((key) => response.fields.map((field) => ({ ...field,
      path: field.path.replace('attributes.', `attributes.${key}[].`), array_domains: [`attributes.${key}[]`],
    })))
    fields.push({ path: 'attributes.provider', display_name: 'Provider', value_schema: { kind: 'enum', values: ['EX'] }, required: true, nullable: false, array_domains: [] })
    api.getProfileMappingOptions.mockResolvedValue({ ...response, fields, capabilities: [{ ...cap,
      metadata: { ...cap.metadata, custom_profile_reuse: { ...cap.metadata.custom_profile_reuse,
        inputs: { ...cap.metadata.custom_profile_reuse.inputs, provider: { ...cap.metadata.custom_profile_reuse.inputs.provider, allow_field: true } } } },
      input_paths: { ...cap.input_paths, provider: ['attributes.provider'], mention: ['attributes.records[].paper_name', 'attributes.other[].paper_name'] },
      output_paths: { identifier: ['attributes.records[].resolved_id', 'attributes.other[].resolved_id'] },
    }] })
    const changed = vi.fn()
    render(<Harness changed={changed} />)
    fireEvent.click(screen.getByRole('button', { name: 'Find compatible validators' }))
    await screen.findByRole('combobox', { name: 'Find validators for canonical field' })
    choose('Find validators for canonical field', 'Paper name · attributes.records[].paper_name · each list item')
    fireEvent.click(screen.getByRole('button', { name: 'Map field to mention · Identifier lookup' }))
    expect(changed.mock.lastCall![0].validator_mappings[0].mode).toBe('per_element')
    choose('Input source · provider · validator_1', 'Canonical profile field')
    choose('Input field · provider · validator_1', 'attributes.provider')
    expect(changed.mock.lastCall![0].validator_mappings[0].inputs.provider.field_path).toBe('attributes.provider')
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Write identifier to · validator_1' }))
    expect(screen.queryByRole('option', { name: 'attributes.other[].resolved_id' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: 'attributes.records[].resolved_id' }))
    expect(changed.mock.lastCall![0].validator_mappings[0].outputs.identifier).toBe('attributes.records[].resolved_id')
  })

  it('retains and explains an exact saved mapping when the catalog fingerprint changes', async () => {
    const changed = vi.fn()
    const saved: GenericProfileContract = { ...contract, validator_mappings: [{
      mapping_id: 'saved', capability_ref: cap.capability_ref, capability_fingerprint: 'sha256:old',
      inputs: { mention: { source: 'field', field_path: 'attributes.paper_name' } }, outputs: { identifier: 'attributes.resolved_id' },
      policy: { unresolved: 'requires_curator_review', blocks_readiness: false }, mode: 'whole',
    }] }
    render(<ProfileValidatorEditor value={saved} onChange={changed} onValidate={vi.fn()} issues={[]} />)
    expect(screen.getByText('Input mention: attributes.paper_name')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Find compatible validators' }))
    await screen.findByText(/not automatically repinned/)
    expect(changed).not.toHaveBeenCalled()
    expect(saved.validator_mappings![0].capability_fingerprint).toBe('sha256:old')
  })
})
