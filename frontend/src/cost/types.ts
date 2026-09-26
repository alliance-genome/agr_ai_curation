export interface Totals {
  attempt_count: number;
  unknown_charge_attempts: number;
  outcomes: Record<string, number>;
  usage: Record<string, { known_total: number | null; known_attempts: number; unknown_attempts: number }>;
  recorded_charges: { unit: string; source: string; amount: string; attempts: number }[];
  estimates: { currency: string; lower: string | null; upper: string | null; priced_attempts: number; unpriced_attempts: number; unavailable_reasons: Record<string, number> };
}
export interface Run extends Totals {
  session_id: string; run_id: string; activity: string; flow_run_id: string | null; started_at: string;
}
export interface CostRequest {
  attempt_id: string; fact_revision: number; created_at: string; session_id: string; run_id: string;
  provider: string; model: string | null; agent_id: string | null; outcome: string; usage_status: string;
  usage: Record<string, number | null>;
  recorded_charge: { amount: string; unit: string; source: string } | null;
  estimate: { cost?: string; estimated_cost_upper?: string; estimate_unavailable_reason?: string; pricing_uncertainty?: string[] };
}
export interface CostReport {
  generated_at: string; deployment_id: string; scope: string; pricing_snapshot_id: string | null;
  pricing_source: string | null; pricing_captured_at: string | null; valuation_algorithm: string;
  coverage: { history: string; excluded: string[]; service_tier: string };
  filters: Record<string, string | null>;
  totals: Totals; runs: Run[]; requests: CostRequest[];
  pagination: { offset: number; page_size: number; request_count: number; run_count: number };
}
