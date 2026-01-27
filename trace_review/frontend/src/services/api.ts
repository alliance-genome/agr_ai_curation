import axios from 'axios';
import { AnalyzeTraceResponse, TraceViewResponse } from '../types';

const API_BASE_URL = '/api';

export const api = {
  // Authentication
  async devBypass() {
    const response = await axios.post(`${API_BASE_URL}/auth/dev-bypass`, {
      dev_key: 'dev'
    });
    return response.data;
  },

  // Trace analysis
  async analyzeTrace(traceId: string, source: 'remote' | 'local' = 'remote'): Promise<AnalyzeTraceResponse> {
    const response = await axios.post(`${API_BASE_URL}/traces/analyze`, {
      trace_id: traceId,
      source: source
    });
    return response.data;
  },

  async getTraceView(traceId: string, viewName: string): Promise<TraceViewResponse> {
    const response = await axios.get(`${API_BASE_URL}/traces/${traceId}/views/${viewName}`);
    return response.data;
  },

  async clearCache(): Promise<{ status: string; message: string; cleared_count: number }> {
    const response = await axios.post(`${API_BASE_URL}/traces/cache/clear`);
    return response.data;
  }
};
