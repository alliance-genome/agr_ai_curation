import { render, screen } from '@/test/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import NodePanelDock from './NodePanelDock'

function renderDock(props: Partial<React.ComponentProps<typeof NodePanelDock>> = {}) {
  const onExpand = vi.fn()
  const onClose = vi.fn()
  render(
    <NodePanelDock
      mode="docked"
      collapsed={false}
      areaWidth={1200}
      railLabel="Gene Extractor"
      onExpand={onExpand}
      onClose={onClose}
      {...props}
    >
      <div>panel body</div>
    </NodePanelDock>
  )
  return { onExpand, onClose }
}

describe('NodePanelDock', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('docks at 440px by default with a keyboard-resizable handle', async () => {
    const user = userEvent.setup()
    renderDock()

    const dock = screen.getByTestId('node-panel-dock')
    expect(dock).toHaveStyle({ width: '440px', flex: '0 0 440px', height: '100%' })
    const handle = screen.getByRole('separator', { name: 'Resize step panel' })
    expect(handle).toHaveAttribute('aria-valuenow', '440')

    handle.focus()
    await user.keyboard('{ArrowLeft}')
    expect(handle).toHaveAttribute('aria-valuenow', '456')
    await user.keyboard('{ArrowRight}{ArrowRight}')
    expect(handle).toHaveAttribute('aria-valuenow', '424')
  })

  it('never exceeds half the canvas area and keeps the 380 minimum while the area allows it', async () => {
    const user = userEvent.setup()
    renderDock({ areaWidth: 800 })

    const handle = screen.getByRole('separator', { name: 'Resize step panel' })
    expect(handle).toHaveAttribute('aria-valuenow', '400')
    handle.focus()
    await user.keyboard('{ArrowLeft}')
    expect(handle).toHaveAttribute('aria-valuenow', '400')
    await user.keyboard('{ArrowRight}{ArrowRight}')
    expect(handle).toHaveAttribute('aria-valuenow', '380')
  })

  it('collapses to a rail that shows the step name and expands again', async () => {
    const user = userEvent.setup()
    const { onExpand } = renderDock({ collapsed: true })

    expect(screen.queryByText('panel body')).not.toBeInTheDocument()
    expect(screen.getByTestId('node-panel-rail')).toHaveTextContent('Gene Extractor')
    expect(screen.getByTestId('node-panel-rail')).toHaveStyle({ flex: '0 0 44px', height: '100%' })
    await user.click(screen.getByRole('button', { name: 'Show panel' }))
    expect(onExpand).toHaveBeenCalledTimes(1)
  })

  it('becomes an in-builder drawer with no portal and no backdrop in drawer mode', async () => {
    const user = userEvent.setup()
    const { onClose } = renderDock({ mode: 'drawer' })

    const drawer = screen.getByTestId('node-panel-drawer')
    expect(drawer).toHaveStyle({ position: 'absolute', top: '0px', right: '0px', bottom: '0px', width: '440px' })
    expect(drawer).toContainElement(screen.getByText('panel body'))
    expect(screen.queryByRole('presentation')).not.toBeInTheDocument()
    expect(document.querySelector('.MuiBackdrop-root')).toBeNull()

    drawer.focus()
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
