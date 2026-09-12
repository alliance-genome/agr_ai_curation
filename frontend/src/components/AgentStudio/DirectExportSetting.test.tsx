import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import DirectExportSetting from './DirectExportSetting'

describe('Direct export choice', () => {
  it('requires an explicit choice and explains inactive prompts and nested values', async () => {
    const change = vi.fn()
    const { rerender } = render(<DirectExportSetting value="ai" onChange={change} />)
    await userEvent.click(screen.getByRole('switch'))
    expect(change).toHaveBeenCalledWith('direct')
    rerender(<DirectExportSetting value="direct" onChange={change} />)
    expect(screen.getByRole('alert')).toHaveTextContent('For this output step only, the agent prompt, group prompts and output instructions are not used')
    await userEvent.click(screen.getByRole('button', { name: 'About direct structured export' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('stock name and supplier')
    expect(screen.getByRole('dialog')).toHaveTextContent('edit the connected extractor agent’s output structure')
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.getByRole('button', { name: 'About direct structured export' })).toHaveAttribute('aria-expanded', 'false')
  })
  it('explains that an agent default does not change existing flow steps', () => {
    render(<DirectExportSetting value="direct" isDefault onChange={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Existing steps keep their settings')
  })
})
