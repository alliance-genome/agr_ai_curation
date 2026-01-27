/**
 * Data Contract: AuditEvent
 *
 * Core data structure for audit events displayed in the audit panel.
 * Represents a single auditable action during AI agent processing.
 */

export type AuditEventType =
  | 'SUPERVISOR_START'      // Supervisor begins processing user request
  | 'SUPERVISOR_DISPATCH'   // Supervisor dispatches a domain
  | 'CREW_START'            // Crew kickoff begins (maps to CrewKickoffStartedEvent)
  | 'AGENT_COMPLETE'        // Agent finishes execution (maps to AgentExecutionCompletedEvent)
  | 'AGENT_GENERATING'      // Agent is generating response text (streaming)
  | 'AGENT_THINKING'        // Agent reasoning/thinking summary (GPT-5 models only)
  | 'TOOL_START'            // Tool usage begins (maps to ToolUsageStartedEvent)
  | 'TOOL_COMPLETE'         // Tool usage finishes (maps to ToolUsageFinishedEvent)
  | 'LLM_CALL'              // LLM thinking/reasoning (maps to LLMCallStartedEvent)
  | 'SUPERVISOR_RESULT'     // Supervisor receives results from a domain
  | 'SUPERVISOR_COMPLETE'   // Supervisor finishes processing (maps to synthesize step)
  | 'SUPERVISOR_ERROR'      // Supervisor encounters error (caught by asyncio.gather)
  | 'SPECIALIST_RETRY'      // Specialist retry attempted (empty output detected)
  | 'SPECIALIST_RETRY_SUCCESS' // Specialist retry succeeded (warning - unusual event)
  | 'SPECIALIST_ERROR'      // Specialist failed to produce output after retry
  | 'FORMATTER_PROCESSING'  // Formatter agent is converting text to structured output
  | 'DOMAIN_PLAN_CREATED'   // Domain execution plan assembled
  | 'DOMAIN_PLANNING'       // Domain begins planning
  | 'DOMAIN_EXECUTION_START'// Domain execution begins
  | 'DOMAIN_COMPLETED'      // Domain finishes execution
  | 'DOMAIN_CATEGORY_ERROR' // Domain task failure
  | 'DOMAIN_SKIPPED'        // Domain skipped due to missing requirements
  | 'FILE_READY'            // File output is ready for download

export interface AuditEvent {
  /**
   * Unique identifier for this event
   * Generated client-side: crypto.randomUUID()
   */
  id: string

  /**
   * Type of audit event (determines display format)
   */
  type: AuditEventType

  /**
   * When this event occurred
   * Used for chronological ordering
   */
  timestamp: Date

  /**
   * Session ID this event belongs to
   * Must match current chat session
   */
  sessionId: string

  /**
   * Event-specific details
   */
  details: AuditEventDetails
}

/**
 * Discriminated union of event details based on type
 * Maps to OpenAI Agents SDK events and supervisor flow mechanics
 */
export type AuditEventDetails =
  | SupervisorStartDetails
  | SupervisorDispatchDetails
  | CrewStartDetails
  | AgentCompleteDetails
  | AgentGeneratingDetails
  | AgentThinkingDetails
  | ToolStartDetails
  | ToolCompleteDetails
  | LLMCallDetails
  | SupervisorResultDetails
  | SupervisorCompleteDetails
  | SupervisorErrorDetails
  | SpecialistRetryDetails
  | SpecialistRetrySuccessDetails
  | SpecialistErrorDetails
  | FormatterProcessingDetails
  | DomainPlanCreatedDetails
  | DomainPlanningDetails
  | DomainExecutionStartDetails
  | DomainCompletedDetails
  | DomainCategoryErrorDetails
  | DomainSkippedDetails
  | FileReadyDetails

export interface SupervisorStartDetails {
  message: string // e.g., "Processing user query"
}

export interface SupervisorDispatchDetails {
  domainName: string     // e.g., "internal_db_domain", "external_api_domain"
  stepNumber: number     // 1-indexed position in execution order
  totalSteps: number     // Total number of domains in execution plan
  isParallel?: boolean   // True if multiple domains running in parallel
}

export interface CrewStartDetails {
  crewName: string          // Name of the crew (from CrewKickoffStartedEvent)
  crewDisplayName?: string  // User-friendly name from backend dispatch dictionary (e.g., "Disease Ontology Crew")
  agents?: string[]         // List of agents in this crew
}

export interface AgentCompleteDetails {
  agentRole: string           // Agent role that completed (from AgentExecutionCompletedEvent)
  agentDisplayName?: string   // User-friendly name from backend dispatch dictionary (e.g., "Disease Ontology Agent")
  crewName?: string           // Which crew this agent belongs to
}

export interface ToolStartDetails {
  toolName: string                // Technical tool name (e.g., "sql_query_tool", "SerperDevTool")
  friendlyName?: string           // User-friendly name from backend dispatch dictionary (e.g., "Searching database...")
  agent?: string                  // Which agent is using this tool
  toolArgs?: Record<string, any>  // Tool inputs from agent tool call
                                  // SQL example: {query: "SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'"}
                                  // API example: {url: "https://api.example.com/endpoint", method: "GET", params: {...}}
}

export interface ToolCompleteDetails {
  toolName: string      // Technical tool name
  friendlyName?: string // User-friendly name (e.g., "Database search complete")
  success?: boolean     // Whether tool succeeded
  error?: string        // Error payload (if tool failed)
  agent?: string        // Which agent is using the tool
}

export interface LLMCallDetails {
  agent?: string        // Which agent is thinking
  message?: string      // Progress message (e.g., "Thinking...")
}

export interface SupervisorResultDetails {
  domainName: string     // Which domain returned results
  stepNumber: number     // Which step completed
  hasError: boolean      // Whether this domain surfaced errors
}

export interface SupervisorCompleteDetails {
  message: string       // e.g., "Query completed successfully"
  totalSteps: number    // Total steps executed
}

export interface SupervisorErrorDetails {
  error: string         // Error message
  crewName?: string     // Which crew encountered error (if specific)
  context?: string      // Additional error context
}

export interface SpecialistRetryDetails {
  specialist: string    // Which specialist is being retried
  reason: string        // Why retry was triggered (e.g., "empty_output")
  output_type: string   // Expected output type (e.g., "GeneExpressionEnvelope")
  message: string       // User-friendly message
}

export interface SpecialistRetrySuccessDetails {
  specialist: string    // Which specialist succeeded on retry
  output_type: string   // Output type that was produced
  output_length: number // Length of the output produced
  message: string       // User-friendly message
}

export interface SpecialistErrorDetails {
  specialist: string    // Which specialist failed
  output_type: string   // Expected output type
  error: string         // Technical error message
  message: string       // User-friendly message with guidance
}

export interface AgentGeneratingDetails {
  agentRole: string           // Agent role that is generating
  agentDisplayName?: string   // User-friendly name
  message?: string            // Progress message (e.g., "Generating response...")
}

export interface AgentThinkingDetails {
  agentRole: string           // Agent role that is thinking
  agentDisplayName?: string   // User-friendly name
  message?: string            // Reasoning/thinking text from GPT-5 models
}

export interface FormatterProcessingDetails {
  formatType: string          // Type of format being processed (e.g., "gene_expression")
  formatDescription?: string  // Description of the format
  message?: string            // Progress message (e.g., "Converting to GeneExpressionEnvelope...")
}

export interface DomainPlanCreatedDetails {
  domains: string[]     // Ordered list of domains in execution plan
}

export interface DomainPlanningDetails {
  domain: string        // Domain entering planning phase
  step: string          // Planner step identifier
}

export interface DomainExecutionStartDetails {
  domain: string        // Domain beginning execution
  total: number         // Total number of tasks scheduled
  tasks: string[]       // Task identifiers
  waves?: number        // Execution waves (if applicable)
}

export interface DomainCompletedDetails {
  domain: string        // Domain completing execution
  success: number       // Successful task count
  total: number         // Total attempted tasks
}

export interface DomainCategoryErrorDetails {
  domain: string        // Domain reporting category error
  task: string          // Task identifier
  error: string         // Error details
}

export interface DomainSkippedDetails {
  domain: string        // Domain skipped
  reason: string        // Reason code / description
}

export interface FileReadyDetails {
  file_id: string       // UUID for API endpoint
  filename: string      // Full filename with extension
  format: string        // File format: csv, tsv, json
  size_bytes?: number   // File size in bytes
  mime_type?: string    // MIME type
  download_url: string  // API endpoint for download
  created_at?: string   // ISO timestamp
}

/**
 * SSE Event Schema (Backend → Frontend)
 */
export interface AuditEventSSE {
  type: AuditEventType
  timestamp: string // ISO 8601 format
  sessionId: string
  details: Record<string, any>
}
