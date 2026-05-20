export type GoFlowEvidencePosture = 'selected_paper' | 'existing_context'

export type GoFlowValidationBadgeStatus = 'resolved' | 'review' | 'context'

export interface GoFlowTermRef {
  id: string
  label: string
}

export interface GoFlowValidationBadge {
  label: string
  status: GoFlowValidationBadgeStatus
}

export interface GoFlowActivityNode {
  id: string
  title: string
  geneProduct: string
  geneId: string
  molecularFunction: GoFlowTermRef
  occursIn?: GoFlowTermRef | null
  partOf?: GoFlowTermRef | null
  inputContext?: GoFlowTermRef | null
  evidencePosture: GoFlowEvidencePosture
  evidencePostureLabel: string
  evidenceCode?: GoFlowTermRef | null
  pmid?: string | null
  doi?: string | null
  sourceSystem: string
  paperSnippet: string
  figurePointer: string
  processBadges: GoFlowTermRef[]
  validationBadges: GoFlowValidationBadge[]
  position: {
    x: number
    y: number
  }
}

export type GoFlowRelationPolarity = 'positive' | 'negative' | 'context'

export interface GoFlowRelationEdge {
  id: string
  source: string
  target: string
  predicate: GoFlowTermRef
  evidencePosture: GoFlowEvidencePosture
  evidencePostureLabel: string
  evidenceCode?: GoFlowTermRef | null
  pmid?: string | null
  doi?: string | null
  sourceSystem: string
  paperSnippet: string
  figurePointer: string
  validationBadges: GoFlowValidationBadge[]
  polarity: GoFlowRelationPolarity
}

export interface GoFlowDemoGraph {
  paper: {
    title: string
    shortLabel: string
    pmid: string
    doi: string
    publicationYear: number
  }
  modelContext: {
    id: string
    title: string
  }
  activities: GoFlowActivityNode[]
  relations: GoFlowRelationEdge[]
}

export interface GoFlowActivityNodeData {
  activity: GoFlowActivityNode
}

export interface GoFlowRelationEdgeData {
  relation: GoFlowRelationEdge
}
