/**
 * Helper Functions for Audit Events
 *
 * Utilities for parsing, formatting, and displaying audit events.
 */

import type {
  AuditEvent,
  AuditEventSSE,
  AuditEventType,
  AuditEventDetails,
  SupervisorStartDetails,
  SupervisorDispatchDetails,
  CrewStartDetails,
  AgentCompleteDetails,
  AgentGeneratingDetails,
  AgentThinkingDetails,
  ToolStartDetails,
  ToolCompleteDetails,
  LLMCallDetails,
  SupervisorResultDetails,
  SupervisorCompleteDetails,
  SupervisorErrorDetails,
  SpecialistRetryDetails,
  SpecialistRetrySuccessDetails,
  SpecialistErrorDetails,
  FormatterProcessingDetails,
  DomainPlanCreatedDetails,
  DomainPlanningDetails,
  DomainExecutionStartDetails,
  DomainCompletedDetails,
  DomainCategoryErrorDetails,
  DomainSkippedDetails,
  FileReadyDetails,
} from '../types/AuditEvent'

export type AuditSeverity = 'info' | 'success' | 'warning' | 'error' | 'processing'

const MAX_PARAMETER_DISPLAY_LENGTH = 500
const MAX_FILTER_DISPLAY = 5

const SPECIES_MAP: Record<string, string> = {
  'NCBITaxon:6239': 'C. elegans',
  'NCBITaxon:7227': 'D. melanogaster',
  'NCBITaxon:9606': 'H. sapiens',
  'NCBITaxon:10090': 'M. musculus',
  'NCBITaxon:7955': 'Z. rerio',
  'NCBITaxon:10116': 'R. norvegicus',
  'NCBITaxon:559292': 'S. cerevisiae'
}

const AGR_ENDPOINT_LABELS: Record<string, string> = {
  allele_search: 'AGR Allele Search',
  gene_search: 'AGR Gene Search',
  disease_search: 'AGR Disease Search'
}

const DOMAIN_DISPLAY_NAMES: Record<string, string> = {
  pdf_domain: 'PDF Analysis',
  internal_db_domain: 'Database Search',
  external_api_domain: 'External APIs'
}

// Map specialist tool names to user-friendly display names
const SPECIALIST_DISPLAY_NAMES: Record<string, string> = {
  ask_pdf_specialist: 'PDF Specialist',
  ask_gene_specialist: 'Gene Specialist',
  ask_allele_specialist: 'Allele Specialist',
  ask_disease_specialist: 'Disease Specialist',
  ask_chemical_specialist: 'Chemical Specialist',
  ask_gene_expression_specialist: 'Gene Expression Specialist',
  ask_gene_ontology_specialist: 'Gene Ontology Specialist',
  ask_go_annotations_specialist: 'GO Annotations Specialist',
  ask_orthologs_specialist: 'Orthologs Specialist',
  ask_ontology_mapping_specialist: 'Ontology Mapping Specialist',
  // File output formatters
  ask_csv_formatter_specialist: 'CSV Formatter',
  ask_tsv_formatter_specialist: 'TSV Formatter',
  ask_json_formatter_specialist: 'JSON Formatter',
}

// Formatter specialists - suppress verbose query display (contains raw data to format)
const FORMATTER_SPECIALISTS = new Set([
  'ask_csv_formatter_specialist',
  'ask_tsv_formatter_specialist',
  'ask_json_formatter_specialist',
])

// Map internal tool names to user-friendly display names
const INTERNAL_TOOL_NAMES: Record<string, string> = {
  // PDF tools
  search_document: 'Search Document',
  read_section: 'Read Section',
  read_subsection: 'Read Subsection',
  // Database tools
  agr_curation_query: 'AGR Curation Query',
  sql_query: 'SQL Query',
  // API tools
  alliance_api_call: 'Alliance API',
  rest_api_call: 'REST API',
}

/**
 * Convert tool name to user-friendly display name.
 * For specialist agents (ask_*), returns the friendly name.
 * For internal tools, returns a cleaner name.
 * For other tools, returns the original name.
 */
function formatToolName(toolName: string): string {
  return SPECIALIST_DISPLAY_NAMES[toolName] || INTERNAL_TOOL_NAMES[toolName] || toolName
}

/**
 * Parses a Server-Sent Event (SSE) into an AuditEvent object.
 *
 * Converts the SSE timestamp string to a Date object and generates a unique ID
 * using crypto.randomUUID(). The resulting AuditEvent is ready for display in the UI.
 *
 * @param sseData - The SSE event data from the backend
 * @returns A fully formed AuditEvent with unique ID and parsed timestamp
 *
 * @example
 * ```ts
 * const sse: AuditEventSSE = {
 *   type: 'SUPERVISOR_START',
 *   timestamp: '2025-10-23T10:30:00.000Z',
 *   sessionId: 'session123',
 *   details: { message: 'Processing user query' }
 * }
 * const event = parseSSEEvent(sse)
 * // event.id is a UUID, event.timestamp is a Date object
 * ```
 */
export function parseSSEEvent(sseData: AuditEventSSE): AuditEvent {
  return {
    id: crypto.randomUUID(),
    type: sseData.type,
    timestamp: new Date(sseData.timestamp),
    sessionId: sseData.sessionId,
    details: sseData.details as AuditEventDetails
  }
}

/**
 * Formats an AuditEvent into a human-readable string for display or copying.
 *
 * Combines the event prefix (e.g., "[SUPERVISOR]") with the event-specific label
 * to create a complete, readable message. Used for displaying events in the UI
 * and for copying to clipboard.
 *
 * @param event - The audit event to format
 * @returns A formatted string like "[SUPERVISOR] Processing user query"
 *
 * @example
 * ```ts
 * const event: AuditEvent = {
 *   type: 'SUPERVISOR_START',
 *   details: { message: 'Processing user query' },
 *   // ... other fields
 * }
 * formatAuditEvent(event) // Returns: "[SUPERVISOR] Processing user query"
 * ```
 */
export function formatAuditEvent(event: AuditEvent): string {
  const prefix = getEventPrefix(event.type, event.details)
  const label = getEventLabel(event)
  return `${prefix} ${label}`
}

/**
 * Returns the text prefix for a given audit event type.
 *
 * Prefixes are plain text labels without emojis, designed for consistent display
 * across all platforms and contexts. Each event type maps to a specific prefix
 * that categorizes the event (SUPERVISOR, CREW, AGENT, TOOL, LLM).
 *
 * For TOOL events, checks if the tool is actually a specialist agent (toolName
 * starts with "ask_") and uses [AGENT] prefix instead.
 *
 * @param type - The audit event type
 * @param details - Optional event details to check for specialist agents
 * @returns A bracketed text prefix like "[SUPERVISOR]" or "[TOOL]"
 *
 * @example
 * ```ts
 * getEventPrefix('SUPERVISOR_START') // Returns: "[SUPERVISOR]"
 * getEventPrefix('TOOL_START')       // Returns: "[TOOL]"
 * getEventPrefix('TOOL_START', { toolName: 'ask_pdf_specialist' }) // Returns: "[AGENT]"
 * getEventPrefix('SUPERVISOR_ERROR') // Returns: "[SUPERVISOR ERROR]"
 * ```
 */
export function getEventPrefix(type: AuditEventType, details?: any): string {
  // Check if this is a specialist agent being called as a tool
  if ((type === 'TOOL_START' || type === 'TOOL_COMPLETE') && details?.toolName) {
    // Specialist agents have toolNames starting with "ask_"
    if (details.toolName.startsWith('ask_')) {
      return '[AGENT]'
    }
    // Internal specialist tools have isSpecialistInternal flag
    if (details.isSpecialistInternal) {
      return '[TOOL]'
    }
  }

  const prefixes: Record<AuditEventType, string> = {
    SUPERVISOR_START: '[SUPERVISOR]',
    SUPERVISOR_DISPATCH: '[SUPERVISOR]',
    SUPERVISOR_RESULT: '[SUPERVISOR]',
    SUPERVISOR_COMPLETE: '[SUPERVISOR]',
    SUPERVISOR_ERROR: '[SUPERVISOR ERROR]',
    SPECIALIST_RETRY: '[SPECIALIST]',
    SPECIALIST_RETRY_SUCCESS: '[SPECIALIST]',
    SPECIALIST_ERROR: '[SPECIALIST ERROR]',
    CREW_START: '[CREW]',
    AGENT_COMPLETE: '[AGENT]',
    AGENT_GENERATING: '[AGENT]',
    AGENT_THINKING: '[AGENT]',
    TOOL_START: '[TOOL]',
    TOOL_COMPLETE: '[TOOL]',
    LLM_CALL: '[LLM]',
    FORMATTER_PROCESSING: '[FORMATTER]',
    DOMAIN_PLAN_CREATED: '[DOMAIN]',
    DOMAIN_PLANNING: '[DOMAIN]',
    DOMAIN_EXECUTION_START: '[DOMAIN]',
    DOMAIN_COMPLETED: '[DOMAIN]',
    DOMAIN_CATEGORY_ERROR: '[DOMAIN ERROR]',
    DOMAIN_SKIPPED: '[DOMAIN]',
    FILE_READY: '[FILE]'
  }
  return prefixes[type]
}

/**
 * Determines the severity level of an audit event for styling purposes.
 *
 * Severity levels control visual appearance (colors, backgrounds) in the UI:
 * - 'error': Red styling for error events (SUPERVISOR_ERROR)
 * - 'success': Green styling for completion events (*_COMPLETE)
 * - 'info': Blue styling for all other events (default)
 *
 * @param type - The audit event type
 * @returns The severity level: 'info', 'success', or 'error'
 *
 * @example
 * ```ts
 * getEventSeverity('SUPERVISOR_ERROR')   // Returns: 'error'
 * getEventSeverity('AGENT_COMPLETE')     // Returns: 'success'
 * getEventSeverity('SUPERVISOR_START')   // Returns: 'info'
 * ```
 */
export function getEventSeverity(type: AuditEventType, details?: any): AuditSeverity {
  if (type.includes('ERROR')) return 'error'

  // Processing events: show animated indicator
  if (type === 'AGENT_GENERATING' || type === 'AGENT_THINKING' || type === 'FORMATTER_PROCESSING') return 'processing'

  // Specialist retry events: warning (something unusual happened)
  if (type === 'SPECIALIST_RETRY') return 'warning'
  if (type === 'SPECIALIST_RETRY_SUCCESS') return 'warning'

  if (type.includes('COMPLETE')) {
    if (details?.success === false) return 'warning'

    if (typeof details?.success === 'number' && typeof details?.total === 'number') {
      if (details.success < details.total) {
        return 'warning'
      }
    }

    if (details?.hasError) return 'warning'
    if (details?.error) return 'warning'

    if (type === 'TOOL_COMPLETE' && typeof details?.friendlyName === 'string') {
      if (details.friendlyName.toLowerCase().includes('failed')) {
        return 'warning'
      }
    }

    return 'success'
  }

  if (type.includes('START') || type === 'LLM_CALL') {
    return 'info'
  }

  if (type === 'SUPERVISOR_RESULT' || type === 'SUPERVISOR_DISPATCH') {
    if (details?.hasError) return 'warning'
    if (details?.error) return 'warning'
    return type === 'SUPERVISOR_RESULT' ? 'success' : 'info'
  }

  if (type.startsWith('DOMAIN_')) {
    if (details?.hasError) return 'warning'
    if (details?.error) return 'warning'
  }

  // File ready is a success event
  if (type === 'FILE_READY') return 'success'

  return 'info'
}

/**
 * Generates a human-readable label for an audit event.
 *
 * Creates detailed, context-aware messages for each event type by extracting
 * relevant information from the event details. Maps to OpenAI Agents SDK events
 * and supervisor flow mechanics. Includes query details (SQL, API params) for
 * TOOL_START events to provide transparency into agent actions.
 *
 * Label formats:
 * - SUPERVISOR_START: The message from details
 * - SUPERVISOR_DISPATCH: "Dispatching crew: {name} (step {N}/{total})"
 * - CREW_START: "Starting crew: {name or agents list}"
 * - AGENT_COMPLETE: "Agent completed: {displayName}"
 * - TOOL_START: "{friendlyName}\nQuery: {SQL}" or "{friendlyName}\n{METHOD} {URL}"
 * - TOOL_COMPLETE: "{friendlyName} complete" (with optional failure indicator)
 * - LLM_CALL: "Thinking..." or custom message
 * - SUPERVISOR_RESULT: "Results from {crew} (step {N})"
 * - SUPERVISOR_COMPLETE: "{message} ({total} steps executed)"
 * - SUPERVISOR_ERROR: "Supervisor error in {crew}: {error}"
 *
 * @param event - The complete audit event with type and details
 * @returns A formatted label string with event-specific information
 *
 * @example
 * ```ts
 * const toolEvent: AuditEvent = {
 *   type: 'TOOL_START',
 *   details: {
 *     toolName: 'sql_query_tool',
 *     friendlyName: 'Searching database...',
 *     toolArgs: { query: "SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'" }
 *   },
 *   // ... other fields
 * }
 * getEventLabel(toolEvent)
 * // Returns: "Searching database...\nQuery: SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'"
 * ```
 */
export function getEventLabel(event: AuditEvent): string {
  switch (event.type) {
    case 'SUPERVISOR_START': {
      return (event.details as SupervisorStartDetails).message
    }

    case 'SUPERVISOR_DISPATCH': {
      const supervDispatch = event.details as SupervisorDispatchDetails
      const parallel = supervDispatch.isParallel ? ' (parallel execution)' : ''
      const domainLabel = formatDomainName(supervDispatch.domainName)
      const step = supervDispatch.stepNumber || 1
      const total = supervDispatch.totalSteps || step
      return `Dispatching domain: ${domainLabel} (step ${step}/${total})${parallel}`
    }

    case 'CREW_START': {
      const crewStart = event.details as CrewStartDetails
      if (crewStart.agents && crewStart.agents.length > 0) {
        return `Starting crew: ${crewStart.agents.join(', ')}`
      }
      const crewLabel = crewStart.crewDisplayName || crewStart.crewName
      return `Starting crew: ${crewLabel}`
    }

    case 'AGENT_COMPLETE': {
      const agentComplete = event.details as AgentCompleteDetails
      const agentLabel = agentComplete.agentDisplayName || agentComplete.agentRole
      const crew = agentComplete.crewName ? ` (${agentComplete.crewName})` : ''
      return `Agent completed: ${agentLabel}${crew}`
    }

    case 'AGENT_GENERATING': {
      const agentGenerating = event.details as AgentGeneratingDetails
      const agentLabel = agentGenerating.agentDisplayName || agentGenerating.agentRole
      return `${agentLabel}: ${agentGenerating.message || 'Agent reasoning'}`
    }

    case 'AGENT_THINKING': {
      const agentThinking = event.details as AgentThinkingDetails
      const agentLabel = agentThinking.agentDisplayName || agentThinking.agentRole
      return `${agentLabel}: ${agentThinking.message || 'Thinking...'}`
    }

    case 'FORMATTER_PROCESSING': {
      const formatter = event.details as FormatterProcessingDetails
      return formatter.message || `Formatting ${formatter.formatType}`
    }

    case 'TOOL_START': {
      const toolStart = event.details as ToolStartDetails
      const isSpecialistAgent = toolStart.toolName.startsWith('ask_')
      const friendlyToolName = formatToolName(toolStart.toolName)

      // Build display name:
      // - Specialist agents: "Calling PDF Specialist..."
      // - Internal tools with agent context: "Gene Specialist: AGR Curation Query"
      // - Other tools: use friendlyName from backend or raw tool name
      let displayName: string
      if (isSpecialistAgent) {
        displayName = `Calling ${friendlyToolName}...`
      } else if (toolStart.agent && INTERNAL_TOOL_NAMES[toolStart.toolName]) {
        // Use our friendly tool name with agent prefix
        displayName = `${toolStart.agent}: ${friendlyToolName}`
      } else {
        displayName = toolStart.friendlyName || friendlyToolName
      }

      // Don't show agent in parentheses - it's either in the displayName or not relevant
      // Skip query details for formatter specialists (contains verbose raw data, not useful)
      if (FORMATTER_SPECIALISTS.has(toolStart.toolName)) {
        return displayName
      }
      const queryDetails = toolStart.toolArgs ? formatToolQuery(toolStart.toolArgs) : ''
      return queryDetails ? `${displayName}\n${queryDetails}` : displayName
    }

    case 'TOOL_COMPLETE': {
      const toolComplete = event.details as ToolCompleteDetails
      const isSpecialistAgent = toolComplete.toolName.startsWith('ask_')
      const friendlyToolName = formatToolName(toolComplete.toolName)

      // Build completion message consistently with TOOL_START
      let completionMessage: string
      if (isSpecialistAgent) {
        completionMessage = `${friendlyToolName} complete`
      } else if (toolComplete.agent && INTERNAL_TOOL_NAMES[toolComplete.toolName]) {
        completionMessage = `${toolComplete.agent}: ${friendlyToolName} complete`
      } else {
        completionMessage = toolComplete.friendlyName || `${friendlyToolName} complete`
      }

      const failed = toolComplete.success === false || Boolean(toolComplete.error)
      const failureText = failed ? ' (failed)' : ''
      const errorText = toolComplete.error ? ` — ${toolComplete.error}` : ''
      return `${completionMessage}${failureText}${errorText}`
    }

    case 'LLM_CALL': {
      const llm = event.details as LLMCallDetails
      const msg = llm.message || 'Thinking...'
      const agentLLM = llm.agent ? ` (${llm.agent})` : ''
      return `${msg}${agentLLM}`
    }

    case 'SUPERVISOR_RESULT': {
      const supervResult = event.details as SupervisorResultDetails
      const domainLabel = formatDomainName(supervResult.domainName)
      const status = supervResult.hasError ? ' ⚠️ with issues' : ''
      return `Results from ${domainLabel} (step ${supervResult.stepNumber})${status}`
    }

    case 'SUPERVISOR_COMPLETE': {
      const complete = event.details as SupervisorCompleteDetails
      return `${complete.message} (${complete.totalSteps} steps executed)`
    }

    case 'SUPERVISOR_ERROR': {
      const supervError = event.details as SupervisorErrorDetails
      const errorCrew = supervError.crewName ? ` in ${supervError.crewName}` : ''
      return `Supervisor error${errorCrew}: ${supervError.error}`
    }

    case 'DOMAIN_PLAN_CREATED': {
      const domainPlan = event.details as DomainPlanCreatedDetails
      return `Domain plan created (${domainPlan.domains.length} domains queued)`
    }

    case 'DOMAIN_PLANNING': {
      const planning = event.details as DomainPlanningDetails
      return `Planning ${formatDomainName(planning.domain)} domain (${planning.step})`
    }

    case 'DOMAIN_EXECUTION_START': {
      const execStart = event.details as DomainExecutionStartDetails
      const domainLabel = formatDomainName(execStart.domain)
      const waveText = execStart.waves ? ` across ${execStart.waves} wave(s)` : ''
      return `Executing ${domainLabel} domain (${execStart.total} tasks${waveText})`
    }

    case 'DOMAIN_COMPLETED': {
      const completed = event.details as DomainCompletedDetails
      const domainLabel = formatDomainName(completed.domain)
      return `${domainLabel} domain complete (${completed.success}/${completed.total} successful)`
    }

    case 'DOMAIN_CATEGORY_ERROR': {
      const domainError = event.details as DomainCategoryErrorDetails
      return `${formatDomainName(domainError.domain)} task failed: ${domainError.task} — ${domainError.error}`
    }

    case 'DOMAIN_SKIPPED': {
      const skipped = event.details as DomainSkippedDetails
      return `${formatDomainName(skipped.domain)} domain skipped (${skipped.reason})`
    }

    case 'SPECIALIST_RETRY': {
      const retry = event.details as SpecialistRetryDetails
      return retry.message || `Retrying ${retry.specialist} (${retry.reason})`
    }

    case 'SPECIALIST_RETRY_SUCCESS': {
      const retrySuccess = event.details as SpecialistRetrySuccessDetails
      return retrySuccess.message || `${retrySuccess.specialist} succeeded on retry`
    }

    case 'SPECIALIST_ERROR': {
      const specialistError = event.details as SpecialistErrorDetails
      return specialistError.message || `${specialistError.specialist} failed: ${specialistError.error}`
    }

    case 'FILE_READY': {
      const fileReady = event.details as FileReadyDetails
      const sizeInfo = fileReady.size_bytes
        ? ` (${(fileReady.size_bytes / 1024).toFixed(1)} KB)`
        : ''
      return `${fileReady.filename} ready for download${sizeInfo}`
    }

    default:
      return 'Unknown event'
  }
}

function formatToolQuery(toolArgs: Record<string, any>): string {
  if (!toolArgs || typeof toolArgs !== 'object') {
    return ''
  }

  if (toolArgs._display_hint === 'agr_api') {
    return formatAGRQuery(toolArgs)
  }

  // Handle agr_curation_query tool (Gene Specialist, Allele Specialist, etc.)
  // AGR curation methods are like "get_gene_by_exact_symbol", "search_alleles", etc.
  // Not HTTP methods like "GET", "POST"
  if (toolArgs.method && AGR_METHOD_LABELS[toolArgs.method]) {
    return formatAGRCurationQuery(toolArgs)
  }

  if (toolArgs.query) {
    return `Query: ${toolArgs.query}`
  }

  // Handle read_subsection: parent_section + subsection
  if (toolArgs.parent_section && toolArgs.subsection) {
    return `Section: ${toolArgs.parent_section} > ${toolArgs.subsection}`
  }

  // Handle read_section: section_name
  if (toolArgs.section_name) {
    return `Section: ${toolArgs.section_name}`
  }

  if (toolArgs.url) {
    const method = toolArgs.method || 'GET'
    let details = `${method} ${toolArgs.url}`

    const parameterSource = toolArgs.body || toolArgs.params
    if (parameterSource && typeof parameterSource === 'object') {
      const formatted = formatParameters(parameterSource, 2, MAX_PARAMETER_DISPLAY_LENGTH)
      if (formatted) {
        details += `\n${formatted}`
      }
    }

    return details
  }

  return ''
}

// Human-readable labels for AGR curation query methods
const AGR_METHOD_LABELS: Record<string, string> = {
  get_gene_by_exact_symbol: 'Gene Lookup (exact)',
  search_genes: 'Gene Search',
  get_gene_by_id: 'Gene by ID',
  get_allele_by_exact_symbol: 'Allele Lookup (exact)',
  search_alleles: 'Allele Search',
  get_allele_by_id: 'Allele by ID',
  get_species: 'Species List',
  get_data_providers: 'Data Providers',
  search_anatomy_terms: 'Anatomy Terms Search',
  search_life_stage_terms: 'Life Stage Terms Search',
  search_go_terms: 'GO Terms Search'
}

function formatAGRCurationQuery(toolArgs: Record<string, any>): string {
  const method = toolArgs.method || 'unknown'
  const methodLabel = AGR_METHOD_LABELS[method] || method
  const lines: string[] = [`AGR Curation: ${methodLabel}`]

  // Add the primary search term
  if (toolArgs.gene_symbol) {
    lines.push(`  Gene: "${toolArgs.gene_symbol}"`)
  }
  if (toolArgs.gene_id) {
    lines.push(`  Gene ID: ${toolArgs.gene_id}`)
  }
  if (toolArgs.allele_symbol) {
    lines.push(`  Allele: "${toolArgs.allele_symbol}"`)
  }
  if (toolArgs.allele_id) {
    lines.push(`  Allele ID: ${toolArgs.allele_id}`)
  }
  if (toolArgs.term) {
    lines.push(`  Term: "${toolArgs.term}"`)
  }

  // Add species/provider context
  if (toolArgs.data_provider) {
    const species = getSpeciesName(PROVIDER_TO_TAXON[toolArgs.data_provider] || '')
    if (species && species !== toolArgs.data_provider) {
      lines.push(`  Species: ${species} (${toolArgs.data_provider})`)
    } else {
      lines.push(`  Provider: ${toolArgs.data_provider}`)
    }
  } else if (toolArgs.taxon_id) {
    lines.push(`  Species: ${getSpeciesName(toolArgs.taxon_id)}`)
  }

  return lines.join('\n')
}

// Provider to taxon mapping (mirrors backend)
const PROVIDER_TO_TAXON: Record<string, string> = {
  'WB': 'NCBITaxon:6239',
  'FB': 'NCBITaxon:7227',
  'MGI': 'NCBITaxon:10090',
  'RGD': 'NCBITaxon:10116',
  'ZFIN': 'NCBITaxon:7955',
  'SGD': 'NCBITaxon:559292',
  'HGNC': 'NCBITaxon:9606',
}

function formatAGRQuery(toolArgs: Record<string, any>): string {
  const method = toolArgs.method || 'GET'
  const endpoint = toolArgs._agr_endpoint ? formatEndpointName(toolArgs._agr_endpoint) : (toolArgs.url || '')
  const lines: string[] = [`${method} ${endpoint}`]

  const body = (toolArgs.body && typeof toolArgs.body === 'object') ? { ...toolArgs.body } : {}
  const summary: string[] = []

  const symbol = body.alleleSymbol || body.symbol
  if (typeof symbol === 'string') {
    summary.push(`Allele/Gene Symbol: "${symbol}"`)
    delete body.alleleSymbol
    delete body.symbol
  }

  if (typeof body.taxonId === 'string') {
    summary.push(`Species: ${getSpeciesName(body.taxonId)}`)
    delete body.taxonId
  }

  if (Array.isArray(body.searchFilters)) {
    const filters = body.searchFilters.slice(0, MAX_FILTER_DISPLAY)
    const more = body.searchFilters.length > MAX_FILTER_DISPLAY ? ` (+${body.searchFilters.length - MAX_FILTER_DISPLAY} more)` : ''
    summary.push(`Filters: ${filters.join(', ')}${more}`)
    delete body.searchFilters
  }

  if (summary.length > 0) {
    lines.push(`  ${summary.join('\n  ')}`)
  }

  if (Object.keys(body).length > 0) {
    const formatted = formatParameters(body, 2, MAX_PARAMETER_DISPLAY_LENGTH)
    if (formatted) {
      lines.push(formatted)
    }
  }

  return lines.join('\n')
}

function formatParameters(params: Record<string, any>, indent = 2, maxLength = MAX_PARAMETER_DISPLAY_LENGTH): string {
  const lines: string[] = ['Parameters:']
  const valueLimit = maxLength > 0 ? Math.min(maxLength, 200) : 200

  const renderEntry = (key: string, value: any, level: number) => {
    if (value === undefined || value === null) {
      return
    }

    const spacing = ' '.repeat(level)

    if (Array.isArray(value)) {
      if (value.length === 0) {
        return
      }
      lines.push(`${spacing}${key}:`)
      value.slice(0, MAX_FILTER_DISPLAY).forEach((entry) => {
        if (typeof entry === 'object') {
          lines.push(`${spacing}- ${truncateValue(JSON.stringify(entry), valueLimit)}`)
        } else {
          lines.push(`${spacing}- ${truncateValue(String(entry), valueLimit)}`)
        }
      })
      if (value.length > MAX_FILTER_DISPLAY) {
        lines.push(`${spacing}- ... (+${value.length - MAX_FILTER_DISPLAY} more)`)
      }
      return
    }

    if (typeof value === 'object') {
      lines.push(`${spacing}${key}:`)
      Object.entries(value).forEach(([childKey, childValue]) => renderEntry(childKey, childValue, level + 2))
      return
    }

    lines.push(`${spacing}${key}: ${truncateValue(String(value), valueLimit)}`)
  }

  Object.entries(params).forEach(([key, value]) => renderEntry(key, value, indent))

  if (lines.length === 1) {
    return ''
  }

  const joined = lines.join('\n')
  if (maxLength > 0 && joined.length > maxLength) {
    return `${joined.substring(0, maxLength)}\n... (truncated)`
  }
  return joined
}

function formatEndpointName(endpoint: string): string {
  return AGR_ENDPOINT_LABELS[endpoint] || endpoint
}

function getSpeciesName(taxonId: string): string {
  return SPECIES_MAP[taxonId] || taxonId
}

function formatDomainName(domain: string): string {
  return DOMAIN_DISPLAY_NAMES[domain] || domain
}

function truncateValue(value: string, maxLength: number): string {
  if (maxLength > 0 && value.length > maxLength) {
    return `${value.substring(0, maxLength)}... (truncated)`
  }
  return value
}
