import { MarkerType, Position, type Edge, type Node } from 'reactflow'

import type {
  GoFlowActivityNodeData,
  GoFlowDemoGraph,
  GoFlowRelationEdgeData,
} from './types'

export type GoFlowReactNode = Node<GoFlowActivityNodeData>
export type GoFlowReactEdge = Edge<GoFlowRelationEdgeData>

export function buildGoFlowDemoGraph(
  graph: GoFlowDemoGraph,
  selection: { kind: 'node' | 'edge'; id: string },
): {
  nodes: GoFlowReactNode[]
  edges: GoFlowReactEdge[]
} {
  const nodes: GoFlowReactNode[] = graph.activities.map((activity) => ({
    id: activity.id,
    type: 'goActivity',
    position: activity.position,
    data: { activity },
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    selected: selection.kind === 'node' && selection.id === activity.id,
  }))

  const edges: GoFlowReactEdge[] = graph.relations.map((relation) => {
    const selected = selection.kind === 'edge' && selection.id === relation.id
    const color = relation.polarity === 'negative'
      ? '#c2410c'
      : relation.polarity === 'positive'
        ? '#047857'
        : '#64748b'
    const opacity = relation.evidencePosture === 'selected_paper' || selected ? 1 : 0.58
    const showCanvasLabel = relation.evidencePosture === 'selected_paper'

    return {
      id: relation.id,
      source: relation.source,
      target: relation.target,
      type: 'smoothstep',
      data: { relation },
      label: showCanvasLabel ? relation.predicate.label : undefined,
      animated: selected,
      selected,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color,
        width: 18,
        height: 18,
      },
      style: {
        stroke: color,
        strokeWidth: selected ? 3 : relation.evidencePosture === 'selected_paper' ? 2.4 : 1.8,
        opacity,
        strokeDasharray: relation.evidencePosture === 'selected_paper' ? undefined : '6 5',
      },
      labelBgPadding: [6, 4],
      labelBgBorderRadius: 4,
      labelBgStyle: {
        fill: '#0f172a',
        fillOpacity: selected ? 0.94 : 0.82,
      },
      labelStyle: {
        fill: '#f8fafc',
        fontSize: 11,
        fontWeight: selected ? 700 : 600,
        letterSpacing: 0,
      },
    }
  })

  return { nodes, edges }
}
