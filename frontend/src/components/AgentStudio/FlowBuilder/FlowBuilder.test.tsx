import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FlowBuilder from './FlowBuilder'

const serviceMocks = vi.hoisted(() => ({
  createFlow: vi.fn(),
  updateFlow: vi.fn(),
  listFlows: vi.fn(),
  getFlow: vi.fn(),
  deleteFlow: vi.fn(),
}))

const invalidationMocks = vi.hoisted(() => ({
  notifyFlowListInvalidated: vi.fn(),
}))

const agentMetadataMocks = vi.hoisted(() => ({
  agents: {} as Record<string, unknown>,
}))

const reactFlowMocks = vi.hoisted(() => ({
  fitView: vi.fn(),
  screenToFlowPosition: vi.fn(({ x, y }: { x: number; y: number }) => ({ x, y })),
}))

vi.mock('@/services/agentStudioService', () => ({
  createFlow: serviceMocks.createFlow,
  updateFlow: serviceMocks.updateFlow,
  listFlows: serviceMocks.listFlows,
  getFlow: serviceMocks.getFlow,
  deleteFlow: serviceMocks.deleteFlow,
}))

vi.mock('@/features/flows/flowListInvalidation', () => ({
  notifyFlowListInvalidated: invalidationMocks.notifyFlowListInvalidated,
}))

vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => agentMetadataMocks,
}))

vi.mock('react-resizable-panels', () => ({
  Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PanelGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PanelResizeHandle: () => <div />,
}))

vi.mock('reactflow', async () => {
  const react = await vi.importActual<typeof import('react')>('react')
  const normalizeNodes = (nodes: unknown[]) =>
    nodes.map((node) => {
      if (
        typeof node !== 'object' ||
        node === null ||
        !('data' in node) ||
        typeof node.data !== 'object' ||
        node.data === null ||
        !('agent_id' in node.data) ||
        node.data.agent_id !== 'task_input'
      ) {
        return node
      }

      return {
        ...node,
        data: {
          ...node.data,
          task_instructions:
            typeof node.data.task_instructions === 'string' && node.data.task_instructions.trim().length > 0
              ? node.data.task_instructions
              : 'Start the flow',
        },
      }
    })

  return {
    __esModule: true,
    default: ({
      children,
      onInit,
      onDrop,
      onDragOver,
    }: {
      children?: React.ReactNode
      onInit?: (instance: {
        fitView: typeof reactFlowMocks.fitView
        screenToFlowPosition: typeof reactFlowMocks.screenToFlowPosition
      }) => void
      onDrop?: (event: React.DragEvent<HTMLDivElement>) => void
      onDragOver?: (event: React.DragEvent<HTMLDivElement>) => void
    }) => {
      react.useEffect(() => {
        onInit?.({
          fitView: reactFlowMocks.fitView,
          screenToFlowPosition: reactFlowMocks.screenToFlowPosition,
        })
      }, [onInit])

      return (
        <div data-testid="react-flow" onDrop={onDrop} onDragOver={onDragOver}>
          {children}
        </div>
      )
    },
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Controls: () => null,
    Background: () => null,
    BackgroundVariant: {
      Dots: 'dots',
    },
    useNodesState: (initialNodes: unknown[]) => {
      const [nodes, setNodesState] = react.useState(normalizeNodes(initialNodes))
      const setNodes = (nextNodes: unknown[] | ((currentNodes: unknown[]) => unknown[])) => {
        setNodesState((currentNodes) =>
          normalizeNodes(
            typeof nextNodes === 'function'
              ? nextNodes(currentNodes)
              : nextNodes
          )
        )
      }

      return [nodes, setNodes, vi.fn()] as const
    },
    useEdgesState: (initialEdges: unknown[]) => {
      const [edges, setEdges] = react.useState(initialEdges)
      return [edges, setEdges, vi.fn()] as const
    },
    addEdge: (params: unknown, edges: unknown[]) => [...edges, params],
  }
})

vi.mock('./FlowNode', () => ({
  default: () => null,
}))

vi.mock('./DeletableEdge', () => ({
  default: () => null,
}))

vi.mock('./AgentPalette', () => ({
  default: () => <div data-testid="agent-palette" />,
}))

vi.mock('./NodeEditor', () => ({
  default: () => null,
}))

vi.mock('./TaskInputEditor', () => ({
  default: () => null,
}))

vi.mock('./PromptViewer', () => ({
  default: () => null,
}))

function buildFlowResponse(overrides: Partial<Parameters<typeof serviceMocks.createFlow>[0]> & {
  id?: string
  name?: string
  description?: string | null
  updated_at?: string
} = {}) {
  return {
    id: overrides.id ?? 'flow-1',
    user_id: 7,
    name: overrides.name ?? 'Fresh Flow',
    description: overrides.description ?? 'Saved from builder',
    execution_count: 0,
    last_executed_at: null,
    created_at: '2026-04-03T00:00:00Z',
    updated_at: overrides.updated_at ?? '2026-04-03T00:00:00Z',
    flow_definition: {
      version: '1.0' as const,
      entry_node_id: 'node_0',
      nodes: [
        {
          id: 'node_0',
          type: 'task_input' as const,
          position: { x: 0, y: 0 },
          data: {
            agent_id: 'task_input',
            agent_display_name: 'Initial Instructions',
            task_instructions: '',
            custom_instructions: '',
            input_source: 'user_query' as const,
            output_key: 'task_input',
          },
        },
      ],
      edges: [],
    },
  }
}

function buildFlowListResponse(name: string) {
  return {
    flows: [
      {
        id: 'flow-1',
        user_id: 7,
        name,
        description: 'Saved from builder',
        step_count: 1,
        execution_count: 0,
        last_executed_at: null,
        created_at: '2026-04-03T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  }
}

describe('FlowBuilder', () => {
  beforeEach(() => {
    serviceMocks.createFlow.mockReset()
    serviceMocks.updateFlow.mockReset()
    serviceMocks.listFlows.mockReset()
    serviceMocks.getFlow.mockReset()
    serviceMocks.deleteFlow.mockReset()
    invalidationMocks.notifyFlowListInvalidated.mockReset()
    reactFlowMocks.fitView.mockClear()
    reactFlowMocks.screenToFlowPosition.mockClear()
    agentMetadataMocks.agents = {}
  })

  it('preserves prompt_version when creating a node from catalog drag data', async () => {
    const user = userEvent.setup()

    serviceMocks.createFlow.mockResolvedValue(buildFlowResponse({ name: 'Versioned Flow' }))
    serviceMocks.listFlows.mockResolvedValue(buildFlowListResponse('Versioned Flow'))

    render(<FlowBuilder />)

    await screen.findByText('1 step')

    const dataTransfer = {
      getData: vi.fn((format: string) => (
        format === 'application/reactflow'
          ? JSON.stringify({
            type: 'agent',
            agentId: 'pdf_extraction',
            agentName: 'PDF Extraction',
            agentDescription: 'Extract text from the uploaded PDF',
            promptVersion: 7,
          })
          : ''
      )),
    }

    fireEvent.drop(screen.getByTestId('react-flow'), {
      clientX: 320,
      clientY: 220,
      dataTransfer,
    })

    await user.click(screen.getByText('File'))

    const fileMenu = await screen.findByRole('menu')
    await user.click(within(fileMenu).getByText('Save'))

    const saveDialog = await screen.findByRole('dialog', { name: 'Save Flow' })
    await user.type(within(saveDialog).getByPlaceholderText('Flow name'), 'Versioned Flow')
    await user.click(within(saveDialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(serviceMocks.createFlow).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Versioned Flow',
          flow_definition: expect.objectContaining({
            nodes: expect.arrayContaining([
              expect.objectContaining({
                type: 'agent',
                data: expect.objectContaining({
                  agent_id: 'pdf_extraction',
                  prompt_version: 7,
                }),
              }),
            ]),
          }),
        })
      )
    })
  }, 15000) // Builder bootstrap plus save dialog interactions can exceed 5s in the full suite.

  it('defaults extraction agents to previous_output when created from the palette', async () => {
    const user = userEvent.setup()

    agentMetadataMocks.agents = {
      allele_extractor: {
        category: 'Extraction',
        subcategory: 'PDF extraction',
      },
    }
    serviceMocks.createFlow.mockResolvedValue(buildFlowResponse({ name: 'Extractor Flow' }))
    serviceMocks.listFlows.mockResolvedValue(buildFlowListResponse('Extractor Flow'))

    render(<FlowBuilder />)

    await screen.findByText('1 step')

    const dataTransfer = {
      getData: vi.fn((format: string) => (
        format === 'application/reactflow'
          ? JSON.stringify({
            type: 'agent',
            agentId: 'allele_extractor',
            agentName: 'Allele Extractor',
            agentDescription: 'Extract allele mentions from the paper',
          })
          : ''
      )),
    }

    fireEvent.drop(screen.getByTestId('react-flow'), {
      clientX: 320,
      clientY: 220,
      dataTransfer,
    })

    await user.click(screen.getByText('File'))
    await user.click(within(await screen.findByRole('menu')).getByText('Save'))

    const saveDialog = await screen.findByRole('dialog', { name: 'Save Flow' })
    await user.type(within(saveDialog).getByPlaceholderText('Flow name'), 'Extractor Flow')
    await user.click(within(saveDialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(serviceMocks.createFlow).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Extractor Flow',
          flow_definition: expect.objectContaining({
            nodes: expect.arrayContaining([
              expect.objectContaining({
                type: 'agent',
                data: expect.objectContaining({
                  agent_id: 'allele_extractor',
                  input_source: 'previous_output',
                }),
              }),
            ]),
          }),
        })
      )
    })
  }, 15000)

  it('refreshes the flow list after creating a new flow and after saving an existing flow', async () => {
    const user = userEvent.setup()

    serviceMocks.createFlow.mockResolvedValue(buildFlowResponse())
    serviceMocks.updateFlow.mockResolvedValue(
      buildFlowResponse({
        name: 'Fresh Flow',
        updated_at: '2026-04-03T01:00:00Z',
      })
    )
    serviceMocks.listFlows
      .mockResolvedValueOnce(buildFlowListResponse('Fresh Flow'))
      .mockResolvedValueOnce(buildFlowListResponse('Fresh Flow'))

    render(<FlowBuilder />)

    await screen.findByText('1 step')

    await user.click(screen.getByText('File'))

    const fileMenu = await screen.findByRole('menu')
    await user.click(within(fileMenu).getByText('Save'))

    const saveDialog = await screen.findByRole('dialog', { name: 'Save Flow' })
    await user.type(within(saveDialog).getByPlaceholderText('Flow name'), 'Fresh Flow')
    await user.click(within(saveDialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(serviceMocks.createFlow).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Fresh Flow',
        })
      )
    })
    await waitFor(() => {
      expect(serviceMocks.listFlows).toHaveBeenCalledTimes(1)
    })
    expect(invalidationMocks.notifyFlowListInvalidated).toHaveBeenCalledWith({
      flowId: 'flow-1',
      reason: 'created',
    })

    await waitFor(() => {
      expect(screen.getByText('Flow saved successfully')).toBeInTheDocument()
    })

    await user.click(screen.getByText('File'))
    await user.click(within(await screen.findByRole('menu')).getByText('Save'))

    await waitFor(() => {
      expect(serviceMocks.updateFlow).toHaveBeenCalledWith(
        'flow-1',
        expect.objectContaining({
          name: 'Fresh Flow',
        })
      )
    })
    await waitFor(() => {
      expect(serviceMocks.listFlows).toHaveBeenCalledTimes(2)
    })
    expect(invalidationMocks.notifyFlowListInvalidated).toHaveBeenLastCalledWith({
      flowId: 'flow-1',
      reason: 'updated',
    })
  }, 15000) // Create-then-update coverage depends on two async refresh cycles and needs more headroom under suite load.

  it('surfaces the shared auth error when opening saved flows', async () => {
    const user = userEvent.setup()

    serviceMocks.listFlows.mockRejectedValue(new Error('Please log in to view your flows'))

    render(<FlowBuilder />)

    await screen.findByText('1 step')

    await user.click(screen.getByText('File'))

    const fileMenu = await screen.findByRole('menu')
    await user.click(within(fileMenu).getByText('Open Flow...'))

    expect(
      await screen.findByText(
        'Please log in to view your flows',
        undefined,
        { timeout: 15000 },
      )
    ).toBeInTheDocument()
  }, 15000)

  it('surfaces unexpected list errors when opening saved flows', async () => {
    const user = userEvent.setup()

    serviceMocks.listFlows.mockRejectedValue(new SyntaxError('Unexpected token < in JSON at position 0'))

    render(<FlowBuilder />)

    await screen.findByText('1 step')

    await user.click(screen.getByText('File'))

    const fileMenu = await screen.findByRole('menu')
    await user.click(within(fileMenu).getByText('Open Flow...'))

    expect(
      await screen.findByText(
        'Unexpected token < in JSON at position 0',
        undefined,
        { timeout: 15000 },
      )
    ).toBeInTheDocument()
  }, 15000)
})
