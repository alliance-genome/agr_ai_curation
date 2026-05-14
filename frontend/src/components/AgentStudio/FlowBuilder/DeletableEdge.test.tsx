import { render, screen } from '@/test/test-utils'
import { fireEvent } from '@testing-library/react'
import type React from 'react'
import { describe, expect, it, vi } from 'vitest'

import DeletableEdge from './DeletableEdge'

vi.mock('reactflow', () => ({
  BaseEdge: ({ style }: { style?: React.CSSProperties }) => (
    <path data-testid="base-edge" style={style} />
  ),
  EdgeLabelRenderer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  getBezierPath: () => ['M 0 0 C 10 10 20 20 30 30', 15, 15],
  useReactFlow: () => ({
    setEdges: vi.fn(),
  }),
}))

const baseProps = {
  id: 'edge-1',
  source: 'node_1',
  target: 'node_2',
  sourceX: 0,
  sourceY: 0,
  targetX: 30,
  targetY: 30,
  sourcePosition: 'bottom' as const,
  targetPosition: 'top' as const,
  selected: false,
  animated: false,
  sourceHandleId: null,
  targetHandleId: null,
  interactionWidth: 20,
}
const TestEdge = DeletableEdge as React.ComponentType<Record<string, unknown>>

describe('DeletableEdge', () => {
  it('renders validation attachment edges with sidecar metadata', () => {
    render(
      <svg>
        <TestEdge
          {...baseProps}
          data={{
            role: 'validation_attachment',
            validationLabel: 'Allele symbol',
          }}
        />
      </svg>
    )

    expect(screen.getByTitle('Allele symbol')).toBeInTheDocument()
    expect(screen.getByTestId('base-edge')).toHaveStyle({
      stroke: '#2e7d32',
      strokeDasharray: '6 4',
    })
  })

  it('uses the Flow Builder delete callback when provided', () => {
    const onDeleteEdge = vi.fn()

    render(
      <svg>
        <TestEdge
          {...baseProps}
          data={{
            role: 'validation_attachment',
            onDeleteEdge,
          }}
        />
      </svg>
    )

    fireEvent.click(screen.getByTitle('Delete connection'))

    expect(onDeleteEdge).toHaveBeenCalledWith('edge-1')
  })
})
