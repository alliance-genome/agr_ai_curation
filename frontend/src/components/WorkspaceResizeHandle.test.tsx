import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Panel, PanelGroup } from 'react-resizable-panels'
import WorkspaceResizeHandle from './WorkspaceResizeHandle'

// The Node entry disables layout effects; exercise the real browser handlers.
vi.mock('react-resizable-panels', async () =>
  vi.importActual('../../node_modules/react-resizable-panels/dist/react-resizable-panels.browser.development.esm.js'),
)

function setup() {
  const onDragging = vi.fn()
  const result = render(
    <PanelGroup direction="horizontal">
      <Panel>Left</Panel>
      <WorkspaceResizeHandle aria-label="Resize workspace" onDragging={onDragging} />
      <Panel>Right</Panel>
    </PanelGroup>,
  )
  const handle = screen.getByRole('separator', { name: 'Resize workspace' })
  handle.setPointerCapture = vi.fn()
  const pointer = new Event('pointerdown', { bubbles: true })
  Object.assign(pointer, { button: 0, isPrimary: true, pointerId: 1 })
  fireEvent(handle, pointer)
  expect(handle.setPointerCapture).toHaveBeenCalledWith(1)
  fireEvent.mouseDown(handle, { button: 0, clientX: 100 })
  expect(handle).toHaveAttribute('data-resize-handle-active', 'pointer')
  return { ...result, handle, onDragging }
}

describe('workspace drag termination', () => {
  it.each(['pointerCancel', 'lostPointerCapture'] as const)('stops on %s', (event) => {
    const { handle, onDragging } = setup()
    fireEvent[event](handle)
    expect(handle).not.toHaveAttribute('data-resize-handle-active', 'pointer')
    expect(onDragging).toHaveBeenLastCalledWith(false)
  })

  it('stops when the window loses focus', () => {
    const { handle } = setup()
    fireEvent.blur(window)
    expect(handle).not.toHaveAttribute('data-resize-handle-active', 'pointer')
  })

  it('cleans up an interrupted drag on unmount', () => {
    const { unmount } = setup()
    unmount()
    const listener = vi.fn()
    window.addEventListener('mouseup', listener)
    fireEvent.blur(window)
    window.removeEventListener('mouseup', listener)
    expect(listener).not.toHaveBeenCalled()
  })
})
