import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import TaskInputEditor from './TaskInputEditor'
import type { AgentNode } from './types'

vi.mock('@/hooks/useAgentIcon', () => ({
  useAgentIcon: () => '📝',
}))

describe('TaskInputEditor', () => {
  it('turns a migrated default into authored instructions when the curator saves', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn()
    const onTaskInstructionsAuthored = vi.fn()
    const node: AgentNode = {
      id: 'task_input_migrated',
      type: 'task_input',
      position: { x: 0, y: 0 },
      data: {
        agent_id: 'task_input',
        agent_display_name: 'Initial Instructions',
        task_instructions: "Execute the 'Legacy Flow' curation workflow.",
        output_key: 'task_input',
      },
    }

    render(
      <TaskInputEditor
        node={node}
        onSave={onSave}
        onClose={vi.fn()}
        onTaskInstructionsAuthored={onTaskInstructionsAuthored}
      />
    )

    const instructions = screen.getByPlaceholderText(/Extract all gene names/i)
    await user.clear(instructions)
    await user.type(instructions, 'Curate only selected alleles from the paper.')
    await user.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onSave).toHaveBeenCalledWith(
      'task_input_migrated',
      expect.objectContaining({
        task_instructions: 'Curate only selected alleles from the paper.',
      })
    )
    expect(onTaskInstructionsAuthored).toHaveBeenCalledOnce()
  })
})
