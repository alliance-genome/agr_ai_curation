import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import ExtractionValidationNotices from './ExtractionValidationNotices'
import { copyNoticeDismissals, extractionValidationNotices } from './extractionValidationNotices'
import type { FlowDefinition, FlowNodeDefinition, ValidationAttachmentSelection } from './types'
import type { AgentMetadata } from '@/services/agentStudioService'
const metadata: Record<string, AgentMetadata> = {
  extract: { name: 'Extract', icon: '', category: 'Extraction' }, validate: { name: 'Validate', icon: '', category: 'Validation' },
  csv_formatter: { name: 'CSV', icon: '', category: 'Output' }, chat: { name: 'Chat', icon: '', category: 'Other' },
}
const node = (id = 'one', agent = 'extract'): FlowNodeDefinition => ({ id, type: 'agent', position: { x: 0, y: 0 }, data: {
  agent_id: agent, agent_display_name: id, output_key: id, validation_attachments: [],
} })
const flow = (...nodes: FlowNodeDefinition[]): FlowDefinition => ({ version: '1.1', nodes, edges: [], entry_node_id: nodes[0]?.id ?? '' })
const check = (overrides = {}): ValidationAttachmentSelection => ({
  attachment_id: 'check', domain_pack_id: 'example', validator_id: 'lookup', validator_binding_id: 'lookup', state: 'active', scope: 'object',
  label: 'Lookup', curator_label: 'Check identity', when_off: null, required: true, blocking: true, default_enabled: true, allow_opt_out: false, enabled: true, ...overrides,
})
const props = (definition: FlowDefinition) => ({ definition, metadata, ownerId: 'user', scopeId: 'flow' })
beforeEach(() => localStorage.clear())
describe('extraction-only notices', () => {
  it('only warns on extraction nodes, including never-configured custom output', () => {
    const custom = node('custom', 'custom_agent')
    custom.data.execution_receipt = { agent_id: 'custom', agent_key: 'custom_agent', agent_revision_id: 'r1', revision: 1, fingerprint: 'fp', output_contract: { output_state: 'structured_extraction', output_mode: 'unprofiled_generic' } }
    render(<ExtractionValidationNotices {...props(flow(node(), custom, node('validator', 'validate'), node('formatter', 'csv_formatter'), node('chat', 'chat')))} />)
    expect(screen.getAllByRole('status')).toHaveLength(2)
  })
  it.each([['automatic', check()], ['custom mapping', check({ validator_binding_id: 'profile-abc-mapping' })], ['unavailable', check({ available: false })]])('does not warn for %s or partial coverage', (_, attachment) => {
    const extractor = node(); extractor.data.validation_attachments = [attachment, check({ attachment_id: 'off', enabled: false })]
    render(<ExtractionValidationNotices {...props(flow(extractor))} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
  it('does not count structural, future or disabled checks', () => {
    const extractor = node(); extractor.data.validation_attachments = [check({ validator_binding_id: undefined }), check({ state: 'under_development' }), check({ enabled: false })]
    expect(extractionValidationNotices(flow(extractor), metadata)[0].hasValidator).toBe(false)
  })
  it('counts a custom validation connection only for its source', () => {
    const definition = flow(node(), node('two'), node('validator', 'validate'))
    definition.edges = [{ id: 'edge', source: 'one', target: 'validator', role: 'validation_attachment', satisfies_binding_id: 'lookup' }]
    expect(extractionValidationNotices(definition, metadata).map(n => n.hasValidator)).toEqual([true, false])
    definition.edges[0].role = 'control_flow'
    expect(extractionValidationNotices(definition, metadata).map(n => n.hasValidator)).toEqual([false, false])
    definition.edges[0].role = 'validation_attachment'; definition.edges[0].target = 'absent'
    expect(extractionValidationNotices(definition, metadata)[0].hasValidator).toBe(false)
  })
  it('uses node selections instead of current head defaults', () => {
    expect(extractionValidationNotices(flow(node()), { ...metadata, extract: { ...metadata.extract, validation_attachments: [check()] } })[0].hasValidator).toBe(false)
  })
  it('dismisses one node and remembers across reload and first save', () => {
    const definition = flow(node(), node('two'))
    const view = render(<ExtractionValidationNotices {...props(definition)} scopeId="draft" />)
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss validation notice for one' }))
    expect(screen.getAllByRole('status')).toHaveLength(1)
    copyNoticeDismissals('user', 'draft', 'saved'); view.unmount()
    render(<ExtractionValidationNotices {...props(definition)} scopeId="saved" />)
    expect(screen.queryByText('one: no database validation configured')).not.toBeInTheDocument()
    expect(screen.getByText('two: no database validation configured')).toBeInTheDocument()
  })
  it('isolates users and flows', () => {
    const view = render(<ExtractionValidationNotices {...props(flow(node()))} />)
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ })); view.unmount()
    const otherUser = render(<ExtractionValidationNotices {...props(flow(node()))} ownerId="other" />)
    expect(screen.getByRole('status')).toBeInTheDocument(); otherUser.unmount()
    render(<ExtractionValidationNotices {...props(flow(node()))} scopeId="other-flow" />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
  it('keeps dismissal through ordinary edits; resets after validation changes', () => {
    const definition = flow(node()); const view = render(<ExtractionValidationNotices {...props(definition)} />)
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    const edited = structuredClone(definition); edited.nodes[0].position.x = 100; edited.nodes[0].data.custom_instructions = 'New'; edited.nodes[0].data.agent_display_name = 'Renamed'
    view.rerender(<ExtractionValidationNotices {...props(edited)} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    const validated = structuredClone(edited); validated.nodes[0].data.validation_attachments = [check()]
    view.rerender(<ExtractionValidationNotices {...props(validated)} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    view.rerender(<ExtractionValidationNotices {...props(edited)} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
  it('forgets deleted nodes and reassesses replacement agents', () => {
    const view = render(<ExtractionValidationNotices {...props(flow(node()))} />)
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    view.rerender(<ExtractionValidationNotices {...props(flow())} />)
    view.rerender(<ExtractionValidationNotices {...props(flow(node()))} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    view.rerender(<ExtractionValidationNotices {...props(flow(node('one', 'another')))} metadata={{ ...metadata, another: metadata.extract }} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
  it('classifies custom validator receipts by canonical schema, not an editable category', () => {
    const validator = node('validator', 'custom_validator')
    validator.data.execution_receipt = { agent_id: 'v', agent_key: 'custom_validator', agent_revision_id: 'r1', revision: 1, fingerprint: 'fp',
      output_contract: { output_state: 'structured_extraction', output_mode: 'domain', output_schema_key: 'FixtureValidatorResult' } }
    const definition = flow(node(), validator)
    definition.edges = [{ id: 'edge', source: 'one', target: 'validator', role: 'validation_attachment', satisfies_binding_id: 'lookup' }]
    const notices = extractionValidationNotices(definition, { ...metadata, custom_validator: { name: 'Custom', icon: '', category: 'Custom' } }, ['FixtureValidatorResult'])
    expect(notices).toHaveLength(1)
    expect(notices[0].hasValidator).toBe(true)
  })
  it('does not classify a pinned extractor using a newer validator or formatter head', () => {
    const extractor = node('one', 'custom')
    extractor.data.execution_receipt = { agent_id: 'c', agent_key: 'custom', agent_revision_id: 'old', revision: 1, fingerprint: 'fp',
      output_contract: { output_state: 'structured_extraction', output_mode: 'unprofiled_generic' } }
    const heads: AgentMetadata[] = [
      { name: 'Custom', icon: '', category: 'Validation', output_schema_key: 'ValidatorResult' },
      { name: 'Custom', icon: '', category: 'Output', output_formatter_format: 'csv' },
    ]
    for (const head of heads) expect(extractionValidationNotices(flow(extractor), { custom: head }, ['ValidatorResult'])).toHaveLength(1)
  })
  it('offers inline help without a checkbox or dialog', () => {
    render(<ExtractionValidationNotices {...props(flow(node()))} />)
    fireEvent.click(screen.getByRole('button', { name: 'Validation help for one' }))
    expect(screen.getByText(/Packaged output formats include/)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })
})
