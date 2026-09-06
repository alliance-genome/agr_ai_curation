import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import FlowProposalSummary from './FlowProposalSummary'
import type { FlowAuthoringProposal } from '@/types/promptExplorer'

function proposal(diff: FlowAuthoringProposal['diff']): FlowAuthoringProposal {
  return {
    contract_version: 'flow_authoring_proposal.v1', base_draft_fingerprint: 'base',
    candidate_draft_fingerprint: 'candidate', change_summary: 'An AI-generated summary', findings: [], diff,
    candidate: { name: 'Expression', description: '', flow_definition: {
      version: '1.1', entry_node_id: 'instructions',
      nodes: [{ id: 'extract', type: 'agent', position: { x: 0, y: 0 }, data: {
        agent_id: 'expression', agent_display_name: 'Gene expression', output_key: 'expression',
      } }], edges: [],
    } },
  }
}

describe('FlowProposalSummary', () => {
  it('shows disabled and removed validators even if the AI summary omits them', () => {
    render(<FlowProposalSummary proposal={proposal([{
      kind: 'changed', path: 'flow_definition.nodes.extract.data.validation_attachments',
      before: [
        { attachment_id: 'gene', curator_label: 'Validate gene identity', enabled: true },
        { attachment_id: 'stage', curator_label: 'Validate developmental stage', enabled: true },
      ],
      after: [{ attachment_id: 'gene', curator_label: 'Validate gene identity', enabled: false }],
    }])} />)
    expect(screen.getByText('Disable: Validate gene identity')).toBeVisible()
    expect(screen.getByText('Remove validation: Validate developmental stage')).toBeVisible()
    expect(screen.queryByText('flow_definition.nodes.extract.data.validation_attachments')).not.toBeInTheDocument()
  })

  it('makes removed steps and omitted evidence visible', () => {
    render(<FlowProposalSummary proposal={proposal([
      { kind: 'removed', path: 'flow_definition.nodes.old', before: { data: { agent_display_name: 'Gene extraction' } } },
      { kind: 'changed', path: 'flow_definition.nodes.extract.data.include_evidence', before: true, after: false },
    ])} />)
    expect(screen.getByText('Remove Gene extraction')).toBeVisible()
    expect(screen.getByText('Leave supporting evidence out of the download')).toBeVisible()
  })

  it('shows the exact agreed instructions in the main preview', () => {
    render(<FlowProposalSummary proposal={proposal([{
      kind: 'changed', path: 'flow_definition.nodes.extract.data.custom_instructions',
      before: '', after: 'Collect only observations in adult brain.',
    }])} />)
    expect(screen.getByText('Additional instructions: Collect only observations in adult brain.')).toBeVisible()
  })
  it('shows disabled active validators when the entire step is new', () => {
    const added = proposal([])
    added.candidate.flow_definition.nodes[0].data.validation_attachments = [{
      attachment_id: 'gene', domain_pack_id: 'gene', validator_id: 'gene', label: 'Gene validation',
      state: 'active', scope: 'field', required: false, blocking: false,
      default_enabled: true, allow_opt_out: true, enabled: false,
      curator_label: 'Validate gene identity', when_off: 'Keep the extracted value.',
    }]
    added.diff = [{ kind: 'added', path: 'flow_definition.nodes.extract', after: added.candidate.flow_definition.nodes[0] }]
    render(<FlowProposalSummary proposal={added} />)
    expect(screen.getByText('Validation turned off: Validate gene identity')).toBeVisible()
  })

  it('shows removed output columns, row filtering and limits in the primary preview', () => {
    const before = { format: 'csv', columns: [{ key: 'gene', header: 'Gene' }, { key: 'tissue', header: 'Tissue', field_ref: 'object.tissue' }], filters: [] }
    const after = { format: 'tsv', columns: [{ key: 'tissue', header: 'Tissue', field_ref: 'object.tissue' }], filters: [{ field_ref: 'object.tissue', op: 'eq', value: 'brain' }], max_rows: 10 }
    const change = proposal([{ kind: 'changed', path: 'flow_definition.nodes.extract.data.projection_plan', before, after }])
    change.candidate.flow_definition.nodes[0].data.projection_plan = after
    render(<FlowProposalSummary proposal={change} />)
    expect(screen.getByText('Remove download columns: Gene')).toBeVisible()
    expect(screen.getByText('Download columns, in order: Tissue')).toBeVisible()
    expect(screen.getByText('Keep only rows where Tissue equals "brain".')).toBeVisible()
    expect(screen.getByText('Output format: TSV')).toBeVisible()
    expect(screen.getByText('Limit the download to 10 rows.')).toBeVisible()
  })

  it('shows column removal when the exact diff targets just the columns property', () => {
    const before = [{ key: 'gene', header: 'Gene' }, { key: 'tissue', header: 'Tissue' }]
    render(<FlowProposalSummary proposal={proposal([{ kind: 'changed', path: 'flow_definition.nodes.extract.data.projection_plan.columns', before, after: [before[0]] }])} />)
    expect(screen.getByText('Remove download columns: Tissue')).toBeVisible()
  })

})


it('distinguishes removing a connection setting from removing the connection', () => {
  const change = proposal([{ kind: 'removed', path: 'flow_definition.edges.connection.condition', before: 'has_results' }])
  change.candidate.flow_definition.edges = [{ id: 'connection', source: 'extract', target: 'output' }]
  render(<FlowProposalSummary proposal={change} />)
  expect(screen.getByText('Update the connection from Gene expression to step')).toBeVisible()
  expect(screen.queryByText('Remove a connection between steps')).not.toBeInTheDocument()
})
