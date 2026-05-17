import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FlowBuilder, { rebuildValidationGroupsFromEdges } from './FlowBuilder'

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
  onConnect: undefined as undefined | ((connection: { source: string; target: string }) => void),
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

      const nodeData = node.data as Record<string, unknown>
      const taskInstructions = nodeData.task_instructions

      return {
        ...node,
        data: {
          ...nodeData,
          task_instructions:
            typeof taskInstructions === 'string' && taskInstructions.trim().length > 0
              ? taskInstructions
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
      onConnect,
    }: {
      children?: React.ReactNode
      onInit?: (instance: {
        fitView: typeof reactFlowMocks.fitView
        screenToFlowPosition: typeof reactFlowMocks.screenToFlowPosition
      }) => void
      onDrop?: (event: React.DragEvent<HTMLDivElement>) => void
      onDragOver?: (event: React.DragEvent<HTMLDivElement>) => void
      onConnect?: (connection: { source: string; target: string }) => void
    }) => {
      react.useEffect(() => {
        onInit?.({
          fitView: reactFlowMocks.fitView,
          screenToFlowPosition: reactFlowMocks.screenToFlowPosition,
        })
      }, [onInit])
      react.useEffect(() => {
        reactFlowMocks.onConnect = onConnect
        return () => {
          reactFlowMocks.onConnect = undefined
        }
      }, [onConnect])

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
    reactFlowMocks.onConnect = undefined
    agentMetadataMocks.agents = {}
  })

  it('reverts validation groups when a custom validator attachment edge is deleted', () => {
    const [extractorNode] = rebuildValidationGroupsFromEdges(
      [
        {
          id: 'node_1',
          type: 'agent',
          position: { x: 0, y: 0 },
          data: {
            agent_id: 'allele_extractor',
            agent_display_name: 'Allele Extractor',
            input_source: 'previous_output',
            output_key: 'alleles',
            validation_attachments: [
              {
                attachment_id: 'allele:symbol',
                domain_pack_id: 'allele',
                validator_id: 'agr.alliance:allele_validation',
                validator_binding_id: 'symbol',
                label: 'Allele symbol lookup',
                state: 'active',
                scope: 'field',
                field_path: 'symbol',
                required: true,
                blocking: false,
                default_enabled: true,
                allow_opt_out: true,
                enabled: true,
              },
              {
                attachment_id: 'allele:identifier',
                domain_pack_id: 'allele',
                validator_id: 'agr.alliance:allele_validation',
                validator_binding_id: 'identifier',
                label: 'Allele identifier lookup',
                state: 'active',
                scope: 'field',
                field_path: 'identifier',
                required: true,
                blocking: true,
                default_enabled: true,
                allow_opt_out: true,
                enabled: true,
              },
            ],
            validation_groups: [
              {
                group_id: 'allele:symbol',
                attachment_id: 'allele:symbol',
                binding_id: 'symbol',
                state: 'replaced',
                edge_id: 'validation_1',
                validator_node_id: 'node_2',
                label: 'Allele symbol lookup',
                required: true,
                blocking: false,
                allow_opt_out: true,
              },
              {
                group_id: 'allele:identifier',
                attachment_id: 'allele:identifier',
                binding_id: 'identifier',
                state: 'replaced',
                edge_id: 'validation_2',
                validator_node_id: 'node_3',
                label: 'Allele identifier lookup',
                required: true,
                blocking: true,
                allow_opt_out: true,
              },
            ],
          },
        },
      ],
      [
        {
          id: 'validation_2',
          source: 'node_1',
          target: 'node_3',
          data: {
            role: 'validation_attachment',
            satisfies_binding_id: 'identifier',
          },
        },
      ]
    )

    expect(extractorNode.data.validation_groups).toEqual([
      expect.objectContaining({
        attachment_id: 'allele:symbol',
        binding_id: 'symbol',
        state: 'automatic',
        edge_id: undefined,
        validator_node_id: undefined,
      }),
      expect.objectContaining({
        attachment_id: 'allele:identifier',
        binding_id: 'identifier',
        state: 'replaced',
        edge_id: 'validation_2',
        validator_node_id: 'node_3',
      }),
    ])
  })

  it('rebuilds automatic validation groups as skipped when the attachment is opted out', () => {
    const [extractorNode] = rebuildValidationGroupsFromEdges(
      [
        {
          id: 'node_1',
          type: 'agent',
          position: { x: 0, y: 0 },
          data: {
            agent_id: 'allele_extractor',
            agent_display_name: 'Allele Extractor',
            input_source: 'previous_output',
            output_key: 'alleles',
            validation_attachments: [
              {
                attachment_id: 'allele:symbol',
                domain_pack_id: 'allele',
                validator_id: 'agr.alliance:allele_validation',
                validator_binding_id: 'symbol',
                label: 'Allele symbol lookup',
                state: 'active',
                scope: 'field',
                field_path: 'symbol',
                required: true,
                blocking: false,
                default_enabled: true,
                allow_opt_out: true,
                enabled: false,
              },
            ],
            validation_groups: [
              {
                group_id: 'allele:symbol',
                attachment_id: 'allele:symbol',
                binding_id: 'symbol',
                state: 'automatic',
                label: 'Allele symbol lookup',
                required: true,
                blocking: false,
                allow_opt_out: true,
              },
            ],
          },
        },
      ],
      []
    )

    expect(extractorNode.data.validation_groups).toEqual([
      expect.objectContaining({
        attachment_id: 'allele:symbol',
        binding_id: 'symbol',
        state: 'skipped',
      }),
    ])
  })

  it('keeps under-development validator metadata out of runtime validation groups', () => {
    const [extractorNode] = rebuildValidationGroupsFromEdges(
      [
        {
          id: 'node_1',
          type: 'agent',
          position: { x: 0, y: 0 },
          data: {
            agent_id: 'allele_extractor',
            agent_display_name: 'Allele Extractor',
            input_source: 'previous_output',
            output_key: 'alleles',
            validation_attachments: [
              {
                attachment_id: 'allele:identifier',
                domain_pack_id: 'allele',
                validator_id: 'agr.alliance:allele_validation',
                validator_binding_id: 'identifier',
                label: 'Allele identifier lookup',
                state: 'active',
                scope: 'field',
                field_path: 'identifier',
                required: true,
                blocking: true,
                default_enabled: true,
                allow_opt_out: true,
                enabled: true,
              },
              {
                attachment_id: 'allele:future-reference',
                domain_pack_id: 'allele',
                validator_id: 'agr.alliance:reference_validation',
                validator_binding_id: 'future-reference',
                label: 'Future reference lookup',
                state: 'under_development',
                scope: 'field',
                field_path: 'reference.curie',
                required: false,
                blocking: false,
                default_enabled: false,
                allow_opt_out: false,
                enabled: false,
                state_explanation: 'Reference validator dispatch is still being wired.',
              },
            ],
            validation_groups: [
              {
                group_id: 'allele:future-reference',
                attachment_id: 'allele:future-reference',
                binding_id: 'future-reference',
                state: 'skipped',
                label: 'Future reference lookup',
                required: false,
                blocking: false,
                allow_opt_out: false,
              },
            ],
          },
        },
      ],
      []
    )

    expect(extractorNode.data.validation_attachments?.map((attachment) => attachment.validator_binding_id)).toEqual([
      'identifier',
      'future-reference',
    ])
    expect(extractorNode.data.validation_groups).toEqual([
      expect.objectContaining({
        attachment_id: 'allele:identifier',
        binding_id: 'identifier',
        state: 'automatic',
      }),
    ])
    expect(extractorNode.data.validation_groups).not.toEqual(expect.arrayContaining([
      expect.objectContaining({
        binding_id: 'future-reference',
        state: 'skipped',
      }),
    ]))
  })

  it('preserves supplemental validation groups for unmatched validator attachment edges', () => {
    const [extractorNode] = rebuildValidationGroupsFromEdges(
      [
        {
          id: 'node_1',
          type: 'agent',
          position: { x: 0, y: 0 },
          data: {
            agent_id: 'allele_extractor',
            agent_display_name: 'Allele Extractor',
            input_source: 'previous_output',
            output_key: 'alleles',
            validation_attachments: [
              {
                attachment_id: 'allele:symbol',
                domain_pack_id: 'allele',
                validator_id: 'agr.alliance:allele_validation',
                validator_binding_id: 'symbol',
                label: 'Allele symbol lookup',
                state: 'active',
                scope: 'field',
                field_path: 'symbol',
                required: true,
                blocking: false,
                default_enabled: true,
                allow_opt_out: true,
                enabled: true,
              },
            ],
            validation_groups: [
              {
                group_id: 'allele:symbol',
                attachment_id: 'allele:symbol',
                binding_id: 'symbol',
                state: 'automatic',
                label: 'Allele symbol lookup',
                required: true,
                blocking: false,
                allow_opt_out: true,
              },
              {
                group_id: 'edge:validation_3',
                attachment_id: null,
                binding_id: 'curator_extra_lookup',
                state: 'supplemental',
                edge_id: 'validation_3',
                validator_node_id: 'node_4',
                label: null,
                required: false,
                blocking: false,
                allow_opt_out: false,
              },
            ],
          },
        },
      ],
      [
        {
          id: 'validation_3',
          source: 'node_1',
          target: 'node_4',
          data: {
            role: 'validation_attachment',
            satisfies_binding_id: 'curator_extra_lookup',
          },
        },
      ]
    )

    expect(extractorNode.data.validation_groups).toEqual([
      expect.objectContaining({
        attachment_id: 'allele:symbol',
        binding_id: 'symbol',
        state: 'automatic',
      }),
      expect.objectContaining({
        group_id: 'edge:validation_3',
        attachment_id: null,
        binding_id: 'curator_extra_lookup',
        state: 'supplemental',
        edge_id: 'validation_3',
        validator_node_id: 'node_4',
        required: false,
        blocking: false,
        allow_opt_out: false,
      }),
    ])
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
        validation_attachments: [
          {
            attachment_id: 'agr.alliance.allele:binding:identifier:field:Allele:allele_identifier',
            domain_pack_id: 'agr.alliance.allele',
            validator_id: 'allele_identifier_lookup',
            validator_binding_id: 'identifier',
            label: 'Allele identifier lookup',
            state: 'active',
            scope: 'field',
            object_type: 'Allele',
            field_path: 'allele_identifier',
            required: true,
            blocking: true,
            default_enabled: true,
            allow_opt_out: true,
          },
          {
            attachment_id: 'agr.alliance.allele:metadata:future',
            domain_pack_id: 'agr.alliance.allele',
            validator_id: 'future_validator',
            label: 'Future validator',
            state: 'under_development',
            scope: 'pack',
            state_explanation: 'Future validation is visible but not runnable yet.',
            required: false,
            blocking: false,
            default_enabled: false,
            allow_opt_out: false,
          },
        ],
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
                  validation_attachments: expect.arrayContaining([
                    expect.objectContaining({
                      attachment_id: 'agr.alliance.allele:binding:identifier:field:Allele:allele_identifier',
                      enabled: true,
                    }),
                    expect.objectContaining({
                      attachment_id: 'agr.alliance.allele:metadata:future',
                      state: 'under_development',
                      enabled: false,
                    }),
                  ]),
                }),
              }),
            ]),
          }),
        })
      )
    })
  }, 15000)

  it('persists validation attachment edges with an explicit selected binding', async () => {
    const user = userEvent.setup()

    agentMetadataMocks.agents = {
      allele_extractor: {
        category: 'Extraction',
        subcategory: 'PDF extraction',
        validation_attachments: [
          {
            attachment_id: 'allele:identifier',
            domain_pack_id: 'agr.alliance.allele',
            domain_pack_version: '0.1.0',
            validator_id: 'allele_identifier_lookup',
            validator_binding_id: 'identifier',
            validator_package_id: 'agr.alliance',
            validator_agent_id: 'allele_validation',
            label: 'Allele identifier lookup',
            target_label: 'Allele identifier',
            state: 'active',
            scope: 'field',
            object_type: 'Allele',
            field_path: 'allele_identifier',
            required: true,
            blocking: true,
            default_enabled: true,
            allow_opt_out: true,
          },
          {
            attachment_id: 'allele:symbol',
            domain_pack_id: 'agr.alliance.allele',
            domain_pack_version: '0.1.0',
            validator_id: 'allele_symbol_lookup',
            validator_binding_id: 'symbol',
            validator_package_id: 'agr.alliance',
            validator_agent_id: 'allele_validation',
            label: 'Allele symbol lookup',
            target_label: 'Allele symbol',
            state: 'active',
            scope: 'field',
            object_type: 'Allele',
            field_path: 'allele_symbol',
            required: false,
            blocking: false,
            default_enabled: true,
            allow_opt_out: true,
          },
        ],
      },
      custom_validator: {
        category: 'Validation',
        subcategory: 'Data Validation',
      },
      custom_validator_two: {
        category: 'Validation',
        subcategory: 'Data Validation',
      },
    }
    serviceMocks.createFlow.mockResolvedValue(buildFlowResponse({ name: 'Validator Edge Flow' }))
    serviceMocks.listFlows.mockResolvedValue(buildFlowListResponse('Validator Edge Flow'))
    const onFlowChange = vi.fn()

    render(<FlowBuilder onFlowChange={onFlowChange} />)

    await screen.findByText('1 step')

    const dropAgent = (agentId: string, agentName: string, agentDescription: string, y: number) => {
      fireEvent.drop(screen.getByTestId('react-flow'), {
        clientX: 320,
        clientY: y,
        dataTransfer: {
          getData: vi.fn((format: string) => (
            format === 'application/reactflow'
              ? JSON.stringify({
                type: 'agent',
                agentId,
                agentName,
                agentDescription,
              })
              : ''
          )),
        },
      })
    }

    dropAgent('allele_extractor', 'Allele Extractor', 'Extract allele mentions', 220)
    dropAgent('custom_validator', 'Custom Validator', 'Validate extracted alleles', 340)
    dropAgent('custom_validator_two', 'Second Custom Validator', 'Validate extracted allele identifiers', 460)

    await waitFor(() => {
      expect(reactFlowMocks.onConnect).toBeTypeOf('function')
    })

    React.act(() => {
      reactFlowMocks.onConnect?.({ source: 'node_1', target: 'node_2' })
    })

    const bindingDialog = (await screen.findByText('Choose Validator Binding')).closest('[role="dialog"]')
    expect(bindingDialog).not.toBeNull()
    await user.click(within(bindingDialog as HTMLElement).getByText('Allele symbol lookup'))

    await waitFor(() => {
      const calls = onFlowChange.mock.calls
      const latestFlowState = calls[calls.length - 1]?.[0]
      expect(latestFlowState?.edges).toEqual(expect.arrayContaining([
        expect.objectContaining({
          source: 'node_1',
          target: 'node_2',
          role: 'validation_attachment',
          satisfies_binding_id: 'symbol',
        }),
      ]))
    })

    React.act(() => {
      reactFlowMocks.onConnect?.({ source: 'node_1', target: 'node_3' })
    })

    const secondBindingDialog = (await screen.findByText('Choose Validator Binding')).closest('[role="dialog"]')
    expect(secondBindingDialog).not.toBeNull()
    await user.click(within(secondBindingDialog as HTMLElement).getByText('Allele identifier lookup'))

    await waitFor(() => {
      const calls = onFlowChange.mock.calls
      const latestFlowState = calls[calls.length - 1]?.[0]
      const extractorNode = latestFlowState?.nodes.find((node: { id: string }) => node.id === 'node_1')
      expect(extractorNode?.validation_groups).toEqual(expect.arrayContaining([
        expect.objectContaining({
          attachment_id: 'allele:symbol',
          binding_id: 'symbol',
          state: 'replaced',
          validator_node_id: 'node_2',
        }),
        expect.objectContaining({
          attachment_id: 'allele:identifier',
          binding_id: 'identifier',
          state: 'replaced',
          validator_node_id: 'node_3',
        }),
      ]))
    })

    await user.click(screen.getByText('File'))
    await user.click(within(await screen.findByRole('menu')).getByText('Save'))

    const saveDialog = await screen.findByRole('dialog', { name: 'Save Flow' })
    await user.type(within(saveDialog).getByPlaceholderText('Flow name'), 'Validator Edge Flow')
    await user.click(within(saveDialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(serviceMocks.createFlow).toHaveBeenCalledWith(
        expect.objectContaining({
          flow_definition: expect.objectContaining({
            edges: expect.arrayContaining([
              expect.objectContaining({
                source: 'node_1',
                target: 'node_2',
                role: 'validation_attachment',
                satisfies_binding_id: 'symbol',
              }),
              expect.objectContaining({
                source: 'node_1',
                target: 'node_3',
                role: 'validation_attachment',
                satisfies_binding_id: 'identifier',
              }),
            ]),
            nodes: expect.arrayContaining([
              expect.objectContaining({
                id: 'node_1',
                data: expect.objectContaining({
                  validation_attachments: expect.arrayContaining([
                    expect.not.objectContaining({
                      export_blocking: expect.anything(),
                    }),
                    expect.objectContaining({
                      validator_binding_id: 'identifier',
                      blocking: true,
                      enabled: true,
                    }),
                  ]),
                }),
              }),
            ]),
          }),
        })
      )
    })
  }, 15000)

  it('does not offer disabled validation bindings as custom replacement targets', async () => {
    const user = userEvent.setup()

    agentMetadataMocks.agents = {
      allele_extractor: {
        category: 'Extraction',
        subcategory: 'PDF extraction',
        validation_attachments: [
          {
            attachment_id: 'allele:identifier',
            domain_pack_id: 'agr.alliance.allele',
            domain_pack_version: '0.1.0',
            validator_id: 'allele_identifier_lookup',
            validator_binding_id: 'identifier',
            validator_package_id: 'agr.alliance',
            validator_agent_id: 'allele_validation',
            label: 'Allele identifier lookup',
            target_label: 'Allele identifier',
            state: 'active',
            scope: 'field',
            object_type: 'Allele',
            field_path: 'allele_identifier',
            required: true,
            blocking: true,
            default_enabled: true,
            allow_opt_out: true,
          },
          {
            attachment_id: 'allele:symbol',
            domain_pack_id: 'agr.alliance.allele',
            domain_pack_version: '0.1.0',
            validator_id: 'allele_symbol_lookup',
            validator_binding_id: 'symbol',
            validator_package_id: 'agr.alliance',
            validator_agent_id: 'allele_validation',
            label: 'Allele symbol lookup',
            target_label: 'Allele symbol',
            state: 'active',
            scope: 'field',
            object_type: 'Allele',
            field_path: 'allele_symbol',
            required: false,
            blocking: false,
            default_enabled: false,
            allow_opt_out: true,
          },
        ],
      },
      custom_validator: {
        category: 'Validation',
        subcategory: 'Data Validation',
      },
    }
    serviceMocks.createFlow.mockResolvedValue(buildFlowResponse({ name: 'Enabled Binding Flow' }))
    serviceMocks.listFlows.mockResolvedValue(buildFlowListResponse('Enabled Binding Flow'))
    const onFlowChange = vi.fn()

    render(<FlowBuilder onFlowChange={onFlowChange} />)

    await screen.findByText('1 step')

    const dropAgent = (agentId: string, agentName: string, agentDescription: string, y: number) => {
      fireEvent.drop(screen.getByTestId('react-flow'), {
        clientX: 320,
        clientY: y,
        dataTransfer: {
          getData: vi.fn((format: string) => (
            format === 'application/reactflow'
              ? JSON.stringify({
                type: 'agent',
                agentId,
                agentName,
                agentDescription,
              })
              : ''
          )),
        },
      })
    }

    dropAgent('allele_extractor', 'Allele Extractor', 'Extract allele mentions', 220)
    dropAgent('custom_validator', 'Custom Validator', 'Validate extracted alleles', 340)

    await waitFor(() => {
      expect(reactFlowMocks.onConnect).toBeTypeOf('function')
    })

    React.act(() => {
      reactFlowMocks.onConnect?.({ source: 'node_1', target: 'node_2' })
    })

    expect(screen.queryByText('Choose Validator Binding')).not.toBeInTheDocument()

    await waitFor(() => {
      const calls = onFlowChange.mock.calls
      const latestFlowState = calls[calls.length - 1]?.[0]
      expect(latestFlowState?.edges).toEqual(expect.arrayContaining([
        expect.objectContaining({
          source: 'node_1',
          target: 'node_2',
          role: 'validation_attachment',
          satisfies_binding_id: 'identifier',
        }),
      ]))
      expect(latestFlowState?.edges).not.toEqual(expect.arrayContaining([
        expect.objectContaining({
          source: 'node_1',
          target: 'node_2',
          role: 'validation_attachment',
          satisfies_binding_id: 'symbol',
        }),
      ]))
    })

    await user.click(screen.getByText('File'))
    await user.click(within(await screen.findByRole('menu')).getByText('Save'))

    const saveDialog = await screen.findByRole('dialog', { name: 'Save Flow' })
    await user.type(within(saveDialog).getByPlaceholderText('Flow name'), 'Enabled Binding Flow')
    await user.click(within(saveDialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(serviceMocks.createFlow).toHaveBeenCalledWith(
        expect.objectContaining({
          flow_definition: expect.objectContaining({
            edges: [
              expect.objectContaining({
                source: 'node_1',
                target: 'node_2',
                role: 'validation_attachment',
                satisfies_binding_id: 'identifier',
              }),
            ],
            nodes: expect.arrayContaining([
              expect.objectContaining({
                id: 'node_1',
                data: expect.objectContaining({
                  validation_attachments: expect.arrayContaining([
                    expect.objectContaining({
                      validator_binding_id: 'symbol',
                      enabled: false,
                    }),
                  ]),
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
