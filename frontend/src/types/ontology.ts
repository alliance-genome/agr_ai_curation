export type IngestionState = "not_indexed" | "indexing" | "ready" | "error";

export interface OntologyStatus {
  ontology_type: string;
  source_id: string;
  state: IngestionState;
  created_at?: string | null;
  updated_at?: string | null;
  message?: Record<string, unknown> | Array<unknown> | string | null;
  term_count: number;
  relation_count: number;
  chunk_count: number;
  embedded_count?: number | null;
}

export interface OntologyIngestionSummary {
  inserted: number;
  relations: number;
  deleted_chunks: number;
  deleted_terms: number;
  deleted_relations: number;
  embedded: number;
  file_info: Record<string, unknown>;
  embedding_summary: Record<string, unknown>;
  insertion_summary: Record<string, number>;
  deletion_summary: Record<string, number>;
}

export interface OntologyIngestionResponse {
  ontology_type: string;
  source_id: string;
  summary: OntologyIngestionSummary;
  status?: OntologyStatus | null;
}

export interface OntologyEmbeddingResponse {
  ontology_type: string;
  source_id: string;
  summary: {
    embedded?: number;
    skipped?: number;
    model?: string;
    source_type?: string;
    source_id?: string;
    total?: number;
    queued?: boolean;
    error?: string;
  };
  status?: OntologyStatus | null;
}
