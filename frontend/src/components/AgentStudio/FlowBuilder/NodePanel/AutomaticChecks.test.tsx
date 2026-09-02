import { render, screen, within } from '@/test/test-utils'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentSelection,
} from '@/test/fixtures/agentStudioDomainEnvelope'
import type { AgentBrowserRequest } from '../types'
import AutomaticChecks from './AutomaticChecks'
import { buildAutomaticChecksView } from './automaticChecks'

const optionalCheck = buildValidationAttachmentSelection({
  attachment_id: 'symbol',
  validator_binding_id: 'symbol_binding',
  validator_agent_id: 'example_validator',
  curator_label: 'Confirm the gene symbol in the reference records',
  description: 'Checks that each gene symbol matches a reference record.',
  when_off: 'The gene symbol stays as the extractor wrote it.',
  allow_opt_out: true,
  blocking: false,
})

const lockedCheck = buildValidationAttachmentSelection({
  attachment_id: 'term',
  validator_binding_id: 'term_binding',
  curator_label: 'Confirm the term against the ontology',
  when_off: null,
  allow_opt_out: false,
  blocking: true,
})

const agentMetadata = {
  example_validator: { name: 'Example Validator', icon: 'V', category: 'Validation' },
}

function renderChecks() {
  const view = buildAutomaticChecksView([optionalCheck, lockedCheck], [], buildDomainEnvelopeMetadata())
  const onToggle = vi.fn<(attachmentIds: string[], enabled: boolean) => void>()
  const onOpenAgent = vi.fn<(request: AgentBrowserRequest) => void>()
  render(
    <AutomaticChecks
      view={view}
      envelopeAgentId="gene_extractor"
      agentMetadata={agentMetadata}
      onToggle={onToggle}
      onOpenAgent={onOpenAgent}
    />
  )
  return { onToggle, onOpenAgent }
}

describe('AutomaticChecks', () => {
  it('counts locked checks in the sentences and lists only the optional one as a switch', async () => {
    const user = userEvent.setup()
    const { onToggle } = renderChecks()

    expect(screen.getByText('2 checks run on what this step extracts.')).toBeInTheDocument()
    expect(screen.getByText('1 check always runs. The 1 below is optional for this flow.')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Adjust optional checks (1)' }))

    const group = screen.getByRole('group', { name: 'Optional checks' })
    const switches = within(group).getAllByRole('switch')
    expect(switches).toHaveLength(1)
    expect(within(group).getByText('Confirm the gene symbol in the reference records')).toBeInTheDocument()
    expect(within(group).queryByText('Confirm the term against the ontology')).not.toBeInTheDocument()

    await user.click(switches[0])
    expect(onToggle).toHaveBeenCalledWith(['symbol'], false)

    await user.click(screen.getByRole('button', { name: 'Hide optional checks' }))
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('opens the info popover from the keyboard, closes on Escape, and returns focus', async () => {
    const user = userEvent.setup()
    const { onOpenAgent } = renderChecks()
    await user.click(screen.getByRole('button', { name: 'Adjust optional checks (1)' }))

    const info = screen.getByRole('button', { name: 'About this check: Confirm the gene symbol in the reference records' })
    info.focus()
    await user.keyboard('{Enter}')

    const dialog = screen.getByRole('dialog', { name: 'Confirm the gene symbol in the reference records' })
    expect(within(dialog).getByText('Checks that each gene symbol matches a reference record.')).toBeInTheDocument()
    expect(within(dialog).getByText('Gene mention evidence · Gene symbol')).toBeInTheDocument()
    expect(within(dialog).getByText('gene_symbol')).toBeInTheDocument()
    expect(within(dialog).getByText('The gene symbol stays as the extractor wrote it.')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Open Example Validator' }))
    expect(onOpenAgent).toHaveBeenCalledWith({ agentId: 'example_validator', tab: 'guide' })

    await user.keyboard('{Enter}')
    const reopened = screen.getByRole('dialog', { name: 'Confirm the gene symbol in the reference records' })
    await user.click(within(reopened).getByRole('button', { name: 'Field details' }))
    expect(onOpenAgent).toHaveBeenCalledWith({
      agentId: 'gene_extractor',
      tab: 'envelope',
      focus: { objectType: 'gene_mention_evidence', fieldPath: 'gene_symbol' },
    })

    await user.keyboard('{Enter}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(info).toHaveFocus()
  })

  it('explains that there is nothing to adjust when every check is locked', () => {
    const view = buildAutomaticChecksView([lockedCheck], [], null)
    render(
      <AutomaticChecks view={view} envelopeAgentId="gene_extractor" agentMetadata={{}} onToggle={vi.fn()} />
    )

    expect(screen.getByText('1 check runs on what this step extracts. It always runs; there is nothing to adjust here.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Adjust optional checks/ })).not.toBeInTheDocument()
  })
})
