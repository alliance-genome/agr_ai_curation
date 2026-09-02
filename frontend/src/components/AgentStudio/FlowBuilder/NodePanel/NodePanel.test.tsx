import { createRef } from 'react'
import { render, screen, waitFor, within } from '@/test/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentSelection,
} from '@/test/fixtures/agentStudioDomainEnvelope'
import type { AgentNode } from '../types'
import NodePanel from './NodePanel'
import type { NodePanelLeaveGuard } from './NodePanel'

const metadataMocks = vi.hoisted(() => ({
  agents: {} as Record<string, unknown>,
}))

vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: metadataMocks.agents,
    refresh: vi.fn(),
    isLoading: false,
    error: null,
  }),
}))

vi.mock('@/hooks/useAgentIcon', () => ({
  useAgentIcon: () => 'AI',
}))

const optionalCheck = buildValidationAttachmentSelection({
  attachment_id: 'symbol',
  validator_binding_id: 'symbol_binding',
  curator_label: 'Confirm the gene symbol in the reference records',
  when_off: 'The gene symbol stays as the extractor wrote it.',
  allow_opt_out: true,
  blocking: false,
})

function buildNode(overrides: Partial<AgentNode['data']> = {}, type: AgentNode['type'] = 'agent'): AgentNode {
  return {
    id: 'node_1',
    type,
    position: { x: 0, y: 0 },
    data: {
      agent_id: 'gene_extractor',
      agent_display_name: 'Gene Extractor',
      output_key: 'gene_output',
      prompt_version: 1,
      validation_attachments: [optionalCheck],
      ...overrides,
    },
  }
}

const extractionMetadata = {
  gene_extractor: {
    name: 'Gene Extractor',
    icon: 'G',
    category: 'PDF Extraction',
    subcategory: 'PDF Extraction',
    domain_envelope: buildDomainEnvelopeMetadata(),
  },
  tsv_formatter: { name: 'TSV File Formatter', icon: 'T', category: 'Output', subcategory: 'Output' },
  custom_validator: { name: 'Custom validator', icon: 'V', category: 'Validation', subcategory: 'Data Validation' },
}

type PanelProps = Partial<React.ComponentProps<typeof NodePanel>>

function renderPanel(node: AgentNode, props: PanelProps = {}) {
  const onApply = vi.fn()
  const onDelete = vi.fn()
  const onHide = vi.fn()
  const onOpenAgent = vi.fn()
  const onTaskInstructionsAuthored = vi.fn()
  render(
    <NodePanel
      node={node}
      stepNumber={2}
      stepCount={4}
      stepNumbersById={{ node_0: 1, node_1: 2 }}
      mode="docked"
      onApply={onApply}
      onDelete={onDelete}
      onHide={onHide}
      onOpenAgent={onOpenAgent}
      onTaskInstructionsAuthored={onTaskInstructionsAuthored}
      {...props}
    />
  )
  return { onApply, onDelete, onHide, onOpenAgent, onTaskInstructionsAuthored }
}

describe('NodePanel', () => {
  beforeEach(() => {
    metadataMocks.agents = extractionMetadata
  })

  it('shows the step header and applies a check opt-out without the deprecated export flag', async () => {
    const user = userEvent.setup()
    const { onApply } = renderPanel(buildNode())

    expect(screen.getByRole('heading', { name: 'Gene Extractor' })).toBeInTheDocument()
    expect(screen.getByTestId('node-panel-step-line')).toHaveTextContent('Step 2 of 4 · gene_extractor v1')
    expect(screen.getByRole('heading', { name: 'Gene Extractor' })).not.toHaveStyle({ whiteSpace: 'nowrap' })
    expect(screen.getByText('Extraction step')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Adjust optional checks (1)' }))
    await user.click(screen.getByRole('switch'))

    // The status pill sits on the kind-label row, beside Cancel and Apply.
    const pill = screen.getByText('Unsaved changes')
    expect(pill.parentElement).toContainElement(screen.getByText('Extraction step'))
    expect(pill.parentElement).toContainElement(screen.getByRole('button', { name: 'Apply' }))
    expect(screen.getByText('1 check runs on what this step extracts, 1 turned off for this flow.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onApply).toHaveBeenCalledTimes(1)
    const [nodeId, payload] = onApply.mock.calls[0]
    expect(nodeId).toBe('node_1')
    expect(payload.output_key).toBe('gene_output')
    expect(payload.validation_attachments[0]).toEqual(expect.objectContaining({ attachment_id: 'symbol', enabled: false }))
    expect(payload.validation_attachments[0]).not.toHaveProperty('export_blocking')
  })

  it('reverts the draft on Cancel', async () => {
    const user = userEvent.setup()
    renderPanel(buildNode({ custom_instructions: 'Original' }))

    const field = screen.getByRole('textbox', { name: 'Instructions for this step' })
    await user.clear(field)
    await user.type(field, 'Changed')
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(field).toHaveValue('Original')
    expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument()
  })

  it('requires task instructions on the input node and reports authored instructions', async () => {
    const user = userEvent.setup()
    const node = buildNode({ agent_id: 'task_input', agent_display_name: 'Initial Instructions', output_key: 'task_input', task_instructions: '', validation_attachments: undefined }, 'task_input')
    const { onApply, onTaskInstructionsAuthored } = renderPanel(node, { stepNumber: 1 })

    expect(screen.getByTestId('node-panel-step-line')).toHaveTextContent('Step 1 of 4 · task input')
    expect(screen.getByText('Task input')).toBeInTheDocument()
    expect(screen.queryByText('About this agent')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()

    await user.type(screen.getByRole('textbox', { name: 'Task instructions' }), 'Extract every gene.')
    await user.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onApply).toHaveBeenCalledWith('node_1', { task_instructions: 'Extract every gene.', output_key: 'task_input' })
    expect(onTaskInstructionsAuthored).toHaveBeenCalledTimes(1)
  })

  it('shows output options and file naming for a file formatter', async () => {
    const user = userEvent.setup()
    const node = buildNode({ agent_id: 'tsv_formatter', agent_display_name: 'TSV File Formatter', output_key: 'tsv_output', validation_attachments: undefined, include_evidence: true })
    const { onApply } = renderPanel(node, {
      outputBinding: { status: 'bound', sources: [{ sourceNodeId: 'node_0', sourceLabel: 'Gene Extractor' }] },
    })

    expect(screen.getByText('Output step')).toBeInTheDocument()
    expect(screen.getByText(/Formats the results of/)).toHaveTextContent('Formats the results of Gene Extractor (step 1).')
    expect(screen.getByRole('switch', { name: 'Include the supporting evidence in the output' })).toBeChecked()

    await user.click(screen.getByRole('radio', { name: 'Custom prefix' }))
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: /Custom prefix/ }), 'results')
    expect(screen.getByText(/results_<node>_<hash>_<trace-id>\.tsv/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Apply' }))
    expect(onApply.mock.calls[0][1]).toEqual(expect.objectContaining({
      output_filename_template: 'results',
      include_evidence: true,
    }))
  })

  it('names the extraction step a custom validator attaches to', () => {
    const node = buildNode({ agent_id: 'custom_validator', agent_display_name: 'Custom validator', validation_attachments: undefined })
    renderPanel(node, { validatorAttachment: { sourceLabel: 'Gene Extractor', sourceStep: 2, replacesLabel: 'Gene lookup' } })

    expect(screen.getByText('Validation step')).toBeInTheDocument()
    expect(screen.getByText(/Attaches to/)).toHaveTextContent('Attaches to Gene Extractor (step 2) and replaces its Gene lookup check for this flow.')
    expect(screen.getByRole('textbox', { name: 'Steering prompt' })).toBeInTheDocument()
  })

  it('links the About row to the Agent Browser tabs', async () => {
    const user = userEvent.setup()
    const { onOpenAgent } = renderPanel(buildNode())

    await user.click(screen.getByRole('button', { name: 'Envelope' }))
    expect(onOpenAgent).toHaveBeenCalledWith({ agentId: 'gene_extractor', tab: 'envelope' })
    await user.click(screen.getByRole('button', { name: 'Prompts' }))
    expect(onOpenAgent).toHaveBeenCalledWith({ agentId: 'gene_extractor', tab: 'prompts' })
  })

  it('offers Delete step in the overflow menu and Hide panel in the header', async () => {
    const user = userEvent.setup()
    const { onDelete, onHide } = renderPanel(buildNode())

    await user.click(screen.getByRole('button', { name: 'More step actions' }))
    await user.click(screen.getByRole('menuitem', { name: 'Delete step' }))
    expect(onDelete).toHaveBeenCalledWith('node_1')

    await user.click(screen.getByRole('button', { name: 'Hide panel' }))
    expect(onHide).toHaveBeenCalledTimes(1)
  })

  it('asks before hiding the panel while edits are unapplied', async () => {
    const user = userEvent.setup()
    const { onHide, onApply } = renderPanel(buildNode({ custom_instructions: '' }))

    await user.type(screen.getByRole('textbox', { name: 'Instructions for this step' }), 'Only the results section.')
    await user.click(screen.getByRole('button', { name: 'Hide panel' }))

    const dialog = await screen.findByRole('dialog', { name: 'Apply changes to step 2?' })
    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }))
    expect(onHide).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Hide panel' }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenCalledTimes(1)
    expect(onHide).toHaveBeenCalledTimes(1)
  })

  it('pins a configuration error under the header', () => {
    renderPanel(buildNode({ hasError: true, errorMessage: 'This step is not connected to the entry path.' }))
    expect(screen.getByText('Configuration error')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('This step is not connected to the entry path.')
  })

  describe('leave guard', () => {
    it('lets the parent leave at once when nothing changed', async () => {
      const guardRef = createRef<NodePanelLeaveGuard>()
      renderPanel(buildNode(), { leaveGuardRef: guardRef })
      await expect(guardRef.current!.requestLeave()).resolves.toBe(true)
    })

    it('asks Apply, Discard, or Keep editing when the draft is dirty', async () => {
      const user = userEvent.setup()
      const guardRef = createRef<NodePanelLeaveGuard>()
      const { onApply } = renderPanel(buildNode(), { leaveGuardRef: guardRef })

      await user.click(screen.getByRole('button', { name: 'Adjust optional checks (1)' }))
      await user.click(screen.getByRole('switch'))

      const keep = guardRef.current!.requestLeave()
      const dialog = await screen.findByRole('dialog', { name: 'Apply changes to step 2?' })
      expect(dialog).toHaveTextContent('You turned off one check. Apply them before you leave this step, or discard them.')
      await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }))
      await expect(keep).resolves.toBe(false)
      expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

      const discard = guardRef.current!.requestLeave()
      await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Discard' }))
      await expect(discard).resolves.toBe(true)
      expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument()
      expect(onApply).not.toHaveBeenCalled()
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

      await user.click(screen.getByRole('switch'))
      const apply = guardRef.current!.requestLeave()
      await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Apply' }))
      await expect(apply).resolves.toBe(true)
      expect(onApply).toHaveBeenCalledTimes(1)
    })
  })
})
