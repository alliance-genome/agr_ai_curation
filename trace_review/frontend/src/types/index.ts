// API Response types

export interface AnalyzeTraceResponse {
  status: string;
  trace_id: string;
  trace_id_short: string;
  message: string;
  cache_status: 'hit' | 'miss';
  cached_at?: string;
  available_views: string[];
}

export interface TraceViewResponse {
  view: string;
  trace_id: string;
  cached_at?: string;
  data: any;
}

// View data types

export interface SummaryData {
  trace_id: string;
  trace_id_short: string;
  trace_name: string;
  duration_seconds: number;
  total_cost: number;
  total_tokens: number;
  observation_count: number;
  score_count: number;
  timestamp: string;
  system_domain?: string;
}

export interface ConversationData {
  user_input: string;
  assistant_response: string;
  trace_id: string;
  trace_name: string;
  session_id?: string;
  timestamp?: string;
}

export interface ToolResultParsed {
  summary: string;
  parsed: {
    summary?: string;
    status?: string;
    count?: number;
    hits?: Array<{
      chunk_id: string;
      section_title: string;
      page_number: number;
      score: number;
      content: string;
    }>;
    data?: Array<Record<string, any>>;
    section?: {
      section_title: string;
      page_numbers: number[];
      chunk_count: number;
      content_preview: string;
      full_content?: string;
    };
    json_data?: Record<string, any> | any[];
    warnings?: null;
    message?: null;
  } | null;
  raw: string;
  parse_status?: 'full' | 'partial' | 'unparsed';
}

export interface ToolCall {
  time: string;
  duration?: string;
  model?: string;
  id: string;
  name: string;
  url: string;
  method: string;
  thought: string;
  status: string;
  status_code: number | string;
  input?: any;
  output?: any;
  tool_result?: ToolResultParsed | null;
  tool_result_length?: number;
}

export interface ToolCallsData {
  total_count: number;
  unique_tools: string[];
  tool_calls: ToolCall[];
}

export interface RoutingPlan {
  needs_pdf: boolean;
  ontologies_needed: string[];
  genes_to_lookup: string[];
  execution_order: string[];
}

export interface FinalSynthesis {
  final_response: string;
  sources_used: string[];
  confidence_level: number;
  model?: string;
}

export interface SubSupervisorRouting {
  observation_id: string;
  observation_name: string;
  observation_type: string;
  timestamp: string;
  actor: string;
  destination: string;
  reasoning?: string;
  confidence?: number;
  model?: string;
}

export interface SupervisorRoutingData {
  found: boolean;
  reasoning: string;
  model?: string;
  routing_plan: RoutingPlan;
  metadata: {
    destination: string;
    confidence: string;
    query_type: string;
  };
  immediate_response?: string;
  final_synthesis?: FinalSynthesis;
  sub_supervisor_routing: SubSupervisorRouting[];
}

export interface Citation {
  chunk_id?: string;
  section_title?: string;
  page_number: number;
  source?: string;
}

export interface ToolCallMetadata {
  tool_name: string;
  query: string;
  citations_count: number;
  call_id: string;
}

export interface PDFCitationsData {
  found: boolean;
  total_citations: number;
  search_queries: string[];
  extracted_content: string;
  citations: Citation[];
  total_chunks_found: number;
  tool_calls: ToolCallMetadata[];
}

// Token Analysis Types
export interface GenerationData {
  generation: number;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number;
  duration_ms?: number;
  output_type: string;
  tool_name?: string;
  time_to_first_token?: number;
  latency?: number;
  observation_id: string;
  timestamp: string;
}

export interface ContextGrowth {
  generation: number;
  prompt_tokens: number;
  delta: number;
}

export interface ModelBreakdown {
  [model: string]: {
    count: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_cost: number;
  };
}

export interface TokenAnalysisData {
  found: boolean;
  total_cost: number;
  total_latency: number;
  total_generations: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  generations: GenerationData[];
  context_growth: ContextGrowth[];
  model_breakdown: ModelBreakdown;
  context_overflow_detected: boolean;
  context_overflow_details?: {
    generation: number;
    prompt_tokens: number;
    model: string;
    timestamp: string;
  };
}

// Agent Context Types
export interface AgentConfig {
  agent_type: string;
  model: string;
  temperature?: number;
  tool_choice?: string;
  reasoning?: any;
  instructions_length: number;
  instructions_preview: string;
  full_instructions?: string;
  tools_available: string[];
  generation_count: number;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: any;
  strict: boolean;
}

export interface AgentContextData {
  found: boolean;
  trace_metadata: {
    supervisor_agent?: string;
    supervisor_model?: string;
    has_document?: boolean;
  };
  supervisor?: AgentConfig;
  specialists: AgentConfig[];
  all_tools: ToolInfo[];
  model_configs: {
    [model: string]: {
      temperature?: number;
      tool_choice?: string;
      reasoning?: any;
    };
  };
}

// Group Context Types (Organization groups - MODs, institutions, teams, etc.)
export interface GroupDetail {
  group_id: string;
  description: string;
}

export interface GroupContextData {
  active_groups: string[];
  injection_active: boolean;
  group_count: number;
  group_details?: GroupDetail[];
}

// Legacy alias for backward compatibility with historical traces
export type ModContextData = GroupContextData;
export type ModDetail = GroupDetail;

// Trace Summary Types
export interface TraceSummaryData {
  trace_info: {
    trace_id: string;
    name: string;
    session_id?: string;
    user_id?: string;
    timestamp: string;
    tags: string[];
    environment?: string;
    bookmarked: boolean;
  };
  query: string;
  document?: {
    id: string;
    name: string;
  };
  response_preview?: string;
  response_length?: number;
  timing: {
    total_latency_seconds: number;
    created_at: string;
    updated_at: string;
  };
  cost: {
    total_cost: number;
    currency: string;
  };
  generation_stats: {
    total_generations: number;
    total_prompt_tokens: number;
    total_completion_tokens: number;
    total_tokens: number;
    models_used: { [model: string]: number };
  };
  tool_summary: {
    total_tool_calls: number;
    tool_counts: { [tool: string]: number };
    unique_tools: string[];
  };
  errors: Array<{
    type: string;
    message: string;
    generation_id?: string;
    model?: string;
    status_message?: string;
  }>;
  has_errors: boolean;
  context_overflow_detected: boolean;
  agent_info: {
    supervisor_agent?: string;
    supervisor_model?: string;
    has_document?: boolean;
    sdk_info?: any;
  };
  group_context?: GroupContextData;
  links: {
    langfuse_trace?: string;
  };
}

// Document Hierarchy Types
export interface HierarchySection {
  name: string;
  page_range: string;
  chunk_count: number;
  subsections: HierarchySubsection[];
}

export interface HierarchySubsection {
  name: string;
  page_range: string;
  chunk_count: number;
}

export interface DocumentHierarchyData {
  found: boolean;
  document_name: string | null;
  structure_type: 'hierarchy' | 'flat' | 'unresolved' | 'unknown';
  top_level_sections: string[];
  sections: HierarchySection[];
  raw_hierarchy_text: string | null;
  chunk_count_total: number;
  error?: string;
  resolution_failed?: boolean;
}

// Agent Configs Types
export interface AgentInstructionStats {
  char_count: number;
  word_count: number;
  line_count: number;
  has_markdown_headings: boolean;
  has_code_blocks: boolean;
  has_bullet_points: boolean;
}

export interface AgentConfigEntry {
  agent_name: string;
  event_name: string;
  model: string;
  tools: string[];
  model_settings: {
    temperature?: number;
    reasoning?: string;
    tool_choice?: string;
    prompt_version?: number;
    // Formatter agent may have separate versions
    base_prompt_version?: number;
    format_prompt_version?: number;
  };
  metadata?: Record<string, any>;
  instructions: string;
  instruction_stats: AgentInstructionStats;
  observation_id?: string;
  timestamp?: string;
}

export interface PromptVersionSummary {
  version: number;
  agent_count: number;
  agents: string[];
}

export interface AgentConfigsData {
  agents: AgentConfigEntry[];
  agent_count: number;
  models_used: string[];
  tools_available: string[];
  // Optional prompt version summary (computed on frontend if not provided)
  prompt_versions?: PromptVersionSummary[];
}
