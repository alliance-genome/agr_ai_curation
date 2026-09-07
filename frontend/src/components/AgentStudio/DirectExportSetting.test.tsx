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
    expect(screen.getByRole('alert')).toHaveTextContent('Agent prompts, group prompts and output instructions do not run')
    expect(screen.getByRole('alert')).toHaveTextContent('CSV and TSV store them as JSON inside a cell')
  })
  it('explains that an agent default does not change existing flow steps', () => {
    render(<DirectExportSetting value="direct" isDefault onChange={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Existing steps keep their settings')
  })
})
