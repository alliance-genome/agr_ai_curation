import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import PromptSection, { type PromptSectionProps } from './PromptSection'

function renderPrompt(overrides: Partial<PromptSectionProps> = {}) {
  const props: PromptSectionProps = {
    parentCorePrompt: 'Locked core contract',
    parentGeneratedContract: 'Locked generated contract',
    parentBasePrompt: 'System base prompt',
    hasTemplate: true,
    templateName: 'Gene Specialist',
    customPrompt: 'System base prompt',
    onCustomPromptChange: vi.fn(),
    onResetToTemplate: vi.fn(),
    overlayStatus: undefined,
    overlayWarning: '',
    availableGroupIds: ['GROUP_C', 'GROUP_A'],
    selectedGroupId: 'GROUP_A',
    onGroupChange: vi.fn(),
    groupPromptOverrides: { GROUP_A: 'GROUP_A override' },
    selectedGroupPrompt: 'GROUP_A override',
    hasSelectedGroupOverride: true,
    onGroupPromptChange: vi.fn(),
    onResetGroupPrompt: vi.fn(),
    includeGroupRules: true,
    onIncludeGroupRulesChange: vi.fn(),
    loggedInAsLabel: 'Doug Howe',
    loggedInGroupIds: ['GROUP_A'],
    onDiscussPromptWithClaude: vi.fn(),
    ...overrides,
  }
  render(<PromptSection {...props} />)
  return props
}

describe('PromptSection', () => {
  it('starts on the editable layer with the template-replacement header', () => {
    renderPrompt()
    expect(screen.getByRole('group', { name: 'Prompt layer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Your prompt/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Editing your main prompt · replaces the template prompt')).toBeInTheDocument()
    expect(screen.getByLabelText('Your prompt')).toHaveValue('System base prompt')
  })

  it('labels the three locked layers with lock and character counts', () => {
    renderPrompt()
    expect(screen.getByRole('button', { name: 'Built-in, read-only, 20 characters' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Output structure, read-only, 25 characters' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Template, read-only, 18 characters' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Your prompt, 18 characters' })).toBeInTheDocument()
  })

  it('shows a locked layer read-only in the same pane', () => {
    renderPrompt()
    fireEvent.click(screen.getByRole('button', { name: /^Built-in/ }))
    expect(screen.getByText('Read-only. Built-in instructions come with the package.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Built-in layer, read-only' })).toHaveTextContent('Locked core contract')
    expect(screen.queryByLabelText('Your prompt')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^Output structure/ }))
    expect(screen.getByRole('region', { name: 'Output structure layer, read-only' })).toHaveTextContent('Locked generated contract')

    fireEvent.click(screen.getByRole('button', { name: /^Template/ }))
    expect(screen.getByText('Read-only. Template instructions come from Gene Specialist.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Template layer, read-only' })).toHaveTextContent('System base prompt')
  })

  it('edits the main prompt and resets it to the template', () => {
    const props = renderPrompt({ customPrompt: 'Edited' })
    fireEvent.change(screen.getByLabelText('Your prompt'), { target: { value: 'Edited more' } })
    expect(props.onCustomPromptChange).toHaveBeenCalledWith('Edited more')
    fireEvent.click(screen.getAllByRole('button', { name: 'Reset to template' })[0])
    expect(props.onResetToTemplate).toHaveBeenCalledTimes(1)
  })

  it('disables Reset to template when the prompt matches the template', () => {
    renderPrompt()
    expect(screen.getAllByRole('button', { name: 'Reset to template' })[0]).toBeDisabled()
  })

  it('shows the overlay review warning above the editor', () => {
    renderPrompt({ overlayStatus: 'needs_review', overlayWarning: 'Prompt contains locked markers.' })
    expect(screen.getByRole('alert')).toHaveTextContent('Prompt contains locked markers.')
  })

  it('renders the group picker with override badges and the runtime toggle', () => {
    const props = renderPrompt()
    expect(screen.getByRole('button', { name: 'GROUP_A, edited' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'GROUP_C' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'GROUP_C' }))
    expect(props.onGroupChange).toHaveBeenCalledWith('GROUP_C')

    const toggle = screen.getByRole('checkbox', { name: 'Add group instructions at runtime' })
    expect(toggle).toBeChecked()
    fireEvent.click(toggle)
    expect(props.onIncludeGroupRulesChange).toHaveBeenCalledWith(false)
  })

  it('edits and resets the selected group override', () => {
    const props = renderPrompt()
    expect(screen.getByText('GROUP_A instructions · your override')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('GROUP_A instructions'), { target: { value: 'New text' } })
    expect(props.onGroupPromptChange).toHaveBeenCalledWith('New text')
    fireEvent.click(screen.getAllByRole('button', { name: 'Reset to template' })[1])
    expect(props.onResetGroupPrompt).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/You are logged in as Doug Howe \(GROUP_A\)\. Overrides: GROUP_A\./)).toBeInTheDocument()
  })

  it('explains when the template has no group instructions', () => {
    renderPrompt({ availableGroupIds: [], selectedGroupId: '', groupPromptOverrides: {} })
    expect(screen.getByText('This template has no group-specific instructions to override.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Group' })).not.toBeInTheDocument()
  })

  it('opens the prompt discussion with Claude', () => {
    const props = renderPrompt()
    fireEvent.click(screen.getByRole('button', { name: 'Discuss prompt changes with Claude' }))
    expect(props.onDiscussPromptWithClaude).toHaveBeenCalledTimes(1)
  })

  it('does not render a Reference section or the old layer names', () => {
    renderPrompt()
    expect(screen.queryByText('Reference')).not.toBeInTheDocument()
    expect(screen.queryByText('Main / base prompt')).not.toBeInTheDocument()
    expect(screen.queryByText('Core Prompt')).not.toBeInTheDocument()
  })
})
