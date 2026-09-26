import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import CostApp, { moneyRange } from './CostApp';

const totals = { attempt_count: 1, unknown_charge_attempts: 1, recorded_charges: [], outcomes: { completed: 1 },
  usage: { total_tokens: { known_total: 100, unknown_attempts: 0, known_attempts: 1 } },
  estimates: { lower: '0.000123456789', upper: '0.000123456789', priced_attempts: 1, unpriced_attempts: 0, unavailable_reasons: {} } };
const report = { generated_at: '2026-09-26T12:00:00Z', deployment_id: 'synthetic', scope: 'time_window', totals,
  pricing_snapshot_id: 'snapshot', pricing_source: 'synthetic fixture', pricing_captured_at: '2026-09-26T00:00:00Z', valuation_algorithm: 'fixture',
  coverage: { excluded: ['infrastructure'], external_services: { pdfx: 'provider_usage_and_charges_unavailable' } }, runs: [{ ...totals, session_id: 'conversation-one', run_id: 'turn-one', activity: 'interactive_chat', flow_run_id: null, started_at: '2026-09-26T12:00:00Z', agents: [] }],
  requests: [], pagination: { offset: 0, page_size: 50, request_count: 1, run_count: 1 } };

beforeEach(() => { window.history.replaceState(null, '', '/cost/'); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('admin cost presentation', () => {
  it('opens a background run without inventing a conversation and drills into its invocation', async () => {
    const background = { ...report, runs: [{ ...report.runs[0], session_id: null, run_id: 'job-run',
      document_id: 'document-one', job_id: 'job-one', activity: 'background', agents: [{ ...totals,
        agent_id: 'validator', agent_name: 'Validator', agent_role: 'validation', node_id: null,
        agent_revision: 'revision-one', invocation_id: 'child-call', parent_invocation_id: 'parent-call', request_ids: ['request-one'] }] }] };
    const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => background });
    vi.stubGlobal('fetch', fetcher);
    render(<CostApp />);
    fireEvent.click(await screen.findByRole('button', { name: 'Run job-run' }));
    await screen.findByRole('heading', { name: 'Agents and flow steps' });
    expect(window.location.search).toContain('run_id=job-run');
    expect(window.location.search).not.toContain('session_id');
    fireEvent.click(screen.getByText('Run job-run · 1 requests'));
    expect(screen.getByText('parent-call')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect invocation' }));
    await waitFor(() => expect(window.location.search).toContain('invocation_id=child-call'));
    fireEvent.click(await screen.findByRole('button', { name: 'Show full run' }));
    await waitFor(() => expect(window.location.search).not.toContain('invocation_id'));
  });
  it.each(['default', null])('shows agent/step and distinct requested versus reported tier: %s', async (tier) => {
    window.history.replaceState(null, '', '/cost/?session_id=conversation-one');
    const request = { attempt_id: 'request-one', fact_revision: 1, created_at: report.generated_at,
      agent_id: 'helper', agent_name: 'Gene helper', agent_role: 'extraction', agent_revision: 'revision-a', node_id: 'step-2',
      provider: 'openai', model: 'test', requested_service_tier: 'flex', effective_service_tier: tier,
      usage: { total_tokens: 100 }, recorded_charge: null, estimate: {}, outcome: 'completed' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ ...report, requests: [request] }) }));
    render(<CostApp />);
    expect(await screen.findByText('Gene helper')).toBeInTheDocument();
    expect(screen.getByText('Flow step: step-2')).toBeInTheDocument();
    expect(screen.getByText('Requested tier: flex')).toBeInTheDocument();
    expect(screen.getByText(`Reported tier: ${tier ?? 'Unknown'}`)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Request details'));
    expect(screen.getByText('revision-a')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Export JSON' })).toHaveAttribute('href', expect.stringContaining('session_id=conversation-one'));
  });
  it('keeps exact monetary strings and unknowns distinct from zero', () => {
    expect(moneyRange('0.0000000000000123', '0.0000000000000123')).toBe('$0.0000000000000123');
    expect(moneyRange('0', '0')).toBe('$0');
    expect(moneyRange(null, null)).toBe('Not priced');
  });
  it.each([
    '?start=2026-09-01T00%3A00%3A00Z&end=2026-09-10T00%3A00%3A00Z&activity=interactive_chat&offset=50',
    '?end=2026-09-10T00%3A00%3A00Z&activity=interactive_chat',
  ])('restores the originating overview query: %s', async (originalQuery) => {
    window.history.replaceState(null, '', '/cost/' + originalQuery);
    const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => report });
    vi.stubGlobal('fetch', fetcher);
    render(<CostApp />);
    expect(await screen.findByText('Selected window subtotal')).toBeInTheDocument();
    expect(screen.getByText('Not reported')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Turn turn-one' }));
    await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.stringContaining('session_id=conversation-one&run_id=turn-one&snapshot_id=snapshot'), expect.anything()));
    expect(window.location.search).not.toContain('start=');
    fireEvent.click(await screen.findByRole('button', { name: 'Back to overview' }));
    await waitFor(() => expect(window.location.search).toBe(originalQuery));
    await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith('/api/admin/cost/reports' + originalQuery, expect.anything()));
  });
  it('distinguishes ordinary-user denial from authentication failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({}) }));
    render(<CostApp />);
    expect(await screen.findByText('Your account does not have admin access.')).toBeInTheDocument();
    expect(screen.queryByText('Recorded charges')).not.toBeInTheDocument();
  });
  it('offers existing login for an expired session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }));
    render(<CostApp />);
    expect(await screen.findByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/api/auth/login?destination=cost');
  });
  it('shows empty coverage rather than a zero-dollar bill', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ ...report, totals: { ...totals, attempt_count: 0 }, runs: [] }) }));
    render(<CostApp />);
    expect(await screen.findByText(/No recorded requests match/)).toBeInTheDocument();
  });
});
