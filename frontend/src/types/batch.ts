/** Column-only batch discovery contract. Detail remains an on-demand request. */
export interface BatchSummary {
  id: string;
  flow_id: string;
  flow_name: string | null;
  status: 'pending' | 'running' | 'completed' | 'cancelled';
  total_documents: number;
  completed_documents: number;
  failed_documents: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface BatchListResponse {
  batches: BatchSummary[];
  total: number;
  next_cursor: string | null;
}

export interface BatchTerminalResponse {
  batches: BatchSummary[];
  window_start: string;
  window_end: string;
  next_cursor: string | null;
  coverage: 'recent_window' | 'reset';
}

export interface BatchLookupResponse {
  batches: BatchSummary[];
  unavailable_ids: string[];
}
