import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import theme, { createAppTheme, type ThemeMode } from '@/theme'
import WorkPaneToolbar from './WorkPaneToolbar'

function renderToolbar(
  props: Partial<React.ComponentProps<typeof WorkPaneToolbar>> = {},
  mode?: ThemeMode,
) {
  const resolvedProps: React.ComponentProps<typeof WorkPaneToolbar> = {
    pendingCount: 2,
    totalCount: 5,
    validatedPendingCount: 1,
    validationCounts: {
      blocking: 2,
      openFindings: 3,
      stale: 1,
      validated: 7,
    },
    isPdfVisible: true,
    selectedDecision: null,
    onAcceptAllValidated: vi.fn(),
    onAddObject: vi.fn(),
    onTogglePdf: vi.fn(),
    ...props,
  }

  render(
    <ThemeProvider theme={mode ? createAppTheme(mode) : theme}>
      <WorkPaneToolbar {...resolvedProps} />
    </ThemeProvider>,
  )

  return resolvedProps
}

describe('WorkPaneToolbar', () => {
  it('shows total and pending counts', () => {
    renderToolbar({ totalCount: 5, pendingCount: 2 })

    expect(screen.getByText(/5 objects/)).toBeInTheDocument()
    expect(screen.getByText(/2 pending/)).toBeInTheDocument()
    expect(screen.getByLabelText('Authoritative validation summary')).toHaveTextContent(
      '7 validated',
    )
    expect(screen.getByLabelText('Authoritative validation summary')).toHaveTextContent('2 need review')
  })

  it('enables Accept all validated only when there are validated pending candidates', () => {
    renderToolbar({ validatedPendingCount: 2 })

    expect(screen.getByRole('button', { name: /accept all validated/i })).toBeEnabled()
  })

  it('disables Accept all validated when none are validated-pending', () => {
    renderToolbar({ validatedPendingCount: 0 })

    expect(screen.getByRole('button', { name: /accept all validated/i })).toBeDisabled()
  })

  it('calls toolbar actions', async () => {
    const user = userEvent.setup()
    const onAcceptAllValidated = vi.fn()
    const onAddObject = vi.fn()
    const onTogglePdf = vi.fn()
    renderToolbar({ onAcceptAllValidated, onAddObject, onTogglePdf })

    await user.click(screen.getByRole('button', { name: /accept all validated/i }))
    await user.click(screen.getByRole('button', { name: /add object/i }))
    await user.click(screen.getByRole('button', { name: /focus grid/i }))

    expect(onAcceptAllValidated).toHaveBeenCalledTimes(1)
    expect(onAddObject).toHaveBeenCalledTimes(1)
    expect(onTogglePdf).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: /validate all/i })).not.toBeInTheDocument()
  })

  it('owns authoritative decisions for the selected pending candidate', async () => {
    const user = userEvent.setup()
    const onAccept = vi.fn()
    const onReject = vi.fn()
    renderToolbar({
      selectedDecision: {
        label: 'Reference one',
        status: 'pending',
        canAccept: true,
        isBusy: false,
        onAccept,
        onReject,
      },
    })

    await user.click(screen.getByRole('button', { name: 'Accept Reference one' }))
    await user.click(screen.getByRole('button', { name: 'Reject Reference one' }))

    expect(onAccept).toHaveBeenCalledTimes(1)
    expect(onReject).toHaveBeenCalledTimes(1)
  })

  it('keeps selected-candidate Accept gated by authoritative validation', () => {
    renderToolbar({
      selectedDecision: {
        label: 'Reference one',
        status: 'pending',
        canAccept: false,
        isBusy: false,
        onAccept: vi.fn(),
        onReject: vi.fn(),
      },
    })

    expect(screen.getByRole('button', { name: 'Accept Reference one' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reject Reference one' })).toBeEnabled()
  })

  it('shows the selected candidate decision without mounting controls after review', () => {
    renderToolbar({
      selectedDecision: {
        label: 'Reference one',
        status: 'accepted',
        canAccept: false,
        isBusy: false,
        onAccept: vi.fn(),
        onReject: vi.fn(),
      },
    })

    expect(screen.getByLabelText('Reference one is accepted')).toHaveTextContent('accepted')
    expect(screen.queryByRole('button', { name: 'Accept Reference one' })).not.toBeInTheDocument()
  })

  it('shows PDF restore without mounting validation execution controls', () => {
    renderToolbar({ isPdfVisible: false })

    expect(screen.getByRole('button', { name: 'Show PDF' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Validate/i })).not.toBeInTheDocument()
  })

  it.each(['light', 'dark'] as const)('uses semantic workspace surfaces in %s mode', (mode) => {
    renderToolbar({}, mode)
    const modeTheme = createAppTheme(mode)

    expect(screen.getByTestId('work-pane-toolbar')).toHaveAttribute('data-theme-mode', mode)
    expect(screen.getByLabelText('Authoritative validation summary')).toBeVisible()
    expect(screen.getByText(/need review/)).toHaveStyle({ color: modeTheme.palette.text.secondary })
    expect(screen.getByText(/stale/)).toHaveStyle({ color: modeTheme.palette.text.secondary })
  })
})
