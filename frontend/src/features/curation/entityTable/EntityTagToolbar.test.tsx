import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import EntityTagToolbar from './EntityTagToolbar'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>{children}</ThemeProvider>
)

describe('EntityTagToolbar', () => {
  it('shows total and pending counts', () => {
    render(
      <EntityTagToolbar totalCount={5} pendingCount={2} validatedPendingCount={1} onAcceptAllValidated={vi.fn()} onAddEntity={vi.fn()} />,
      { wrapper },
    )
    expect(screen.getByText(/5 entities/)).toBeInTheDocument()
    expect(screen.getByText(/2 pending/)).toBeInTheDocument()
  })

  it('calls onAcceptAllValidated when button is clicked', () => {
    const onAcceptAllValidated = vi.fn()
    render(
      <EntityTagToolbar totalCount={5} pendingCount={2} validatedPendingCount={1} onAcceptAllValidated={onAcceptAllValidated} onAddEntity={vi.fn()} />,
      { wrapper },
    )
    fireEvent.click(screen.getByRole('button', { name: /Accept All Validated/ }))
    expect(onAcceptAllValidated).toHaveBeenCalled()
  })

  it('calls onAddEntity when button is clicked', () => {
    const onAddEntity = vi.fn()
    render(
      <EntityTagToolbar totalCount={5} pendingCount={2} validatedPendingCount={1} onAcceptAllValidated={vi.fn()} onAddEntity={onAddEntity} />,
      { wrapper },
    )
    fireEvent.click(screen.getByRole('button', { name: /Add Entity/ }))
    expect(onAddEntity).toHaveBeenCalled()
  })

  it('disables accept all when no pending tags', () => {
    render(
      <EntityTagToolbar totalCount={3} pendingCount={1} validatedPendingCount={0} onAcceptAllValidated={vi.fn()} onAddEntity={vi.fn()} />,
      { wrapper },
    )
    expect(screen.getByRole('button', { name: /Accept All Validated/ })).toBeDisabled()
  })
})
