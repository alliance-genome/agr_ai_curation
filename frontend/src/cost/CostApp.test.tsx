import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import CostApp, { moneyRange } from './CostApp';
import CostTheme from './CostTheme';
import Button from '@mui/material/Button';
import { getContrastRatio } from '@mui/material/styles';

const totals = { attempt_count: 1, unknown_charge_attempts: 1, recorded_charges: [], outcomes: { completed: 1 },
  usage: { total_tokens: { known_total: 100, unknown_attempts: 0, known_attempts: 1 } },
  estimates: { lower: '0.000123456789', upper: '0.000123456789', priced_attempts: 1, unpriced_attempts: 0, unavailable_reasons: {} } };
const report = { generated_at: '2026-09-26T12:00:00Z', deployment_id: 'synthetic', scope: 'time_window', totals,
  pricing_snapshot_id: 'snapshot', pricing_source: 'synthetic fixture', pricing_captured_at: '2026-09-26T00:00:00Z', valuation_algorithm: 'fixture',
  coverage: { excluded: ['infrastructure'], external_services: { pdfx: 'provider_usage_and_charges_unavailable' } }, runs: [{ ...totals, session_id: 'conversation-one', run_id: 'turn-one', activity: 'interactive_chat', flow_run_id: null, started_at: '2026-09-26T12:00:00Z', agents: [] }],
  groups: [{ ...totals, id: 'conversation-one', label: 'conversation-one', session_id: 'conversation-one', workflow_id: null, run_id: null, run_count: 2, conversation_count: 1, last_active: '2026-09-26T12:00:00Z' }],
  requests: [], pagination: { offset: 0, page_size: 50, request_count: 1, run_count: 1, group_count: 1 } };

beforeEach(() => { window.history.replaceState(null, '', '/cost/'); localStorage.clear(); });
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
  it('rounds only displayed dollars to cents and keeps unknowns distinct from zero', () => {
    expect(moneyRange('0.0000000000000123', '0.0000000000000123')).toBe('$0.00');
    expect(moneyRange('0', '0')).toBe('$0.00');
    expect(moneyRange('1.005', '1.005')).toBe('$1.01');
    expect(moneyRange('0.1234', '0.9876')).toBe('$0.12 – $0.99');
    expect(moneyRange('0.121', '0.124')).toBe('$0.12');
    expect(moneyRange('1.23E-16', '1.23E-16')).toBe('$0.00');
    expect(moneyRange(null, null)).toBe('Not priced');
  });
  it('toggles and persists dark mode', () => {
    const { unmount } = render(<CostTheme><p>Dashboard</p></CostTheme>);
    fireEvent.click(screen.getByRole('button', { name: 'Dark mode' }));
    expect(document.documentElement.dataset.costTheme).toBe('dark');
    expect(localStorage.getItem('cost-theme:v1')).toBe('dark');
    unmount();
    render(<CostTheme><p>Dashboard</p></CostTheme>);
    expect(screen.getByRole('button', { name: 'Dark mode' })).toHaveAttribute('aria-pressed', 'true');
  });
  it('keeps dark filled button labels readable without darkening outlined text', () => {
    localStorage.setItem('cost-theme:v1', 'dark');
    render(<CostTheme><Button variant="contained">Apply</Button><Button variant="outlined">Other view</Button></CostTheme>);
    const filled = getComputedStyle(screen.getByRole('button', { name: 'Apply' }));
    // jsdom retains MUI color variables rather than resolving them like a browser.
    const label = filled.getPropertyValue('--variant-containedColor');
    expect(getContrastRatio(label, filled.getPropertyValue('--variant-containedBg'))).toBeGreaterThanOrEqual(4.5);
    const outlined = getComputedStyle(screen.getByRole('button', { name: 'Other view' }));
    expect(outlined.getPropertyValue('--variant-outlinedColor')).toBe('#3b82f6');
  });
  it.each(['flows', 'chats', 'curators'])('offers a first-class %s view and preserves it when filtering', async (view) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => report }));
    render(<CostApp />);
    await screen.findByText('Selected window subtotal');
    fireEvent.click(screen.getByRole('button', { name: view[0].toUpperCase() + view.slice(1) }));
    await screen.findByRole('heading', { name: /Spend by/ });
    expect(window.location.search).toContain(`view=${view}`);
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await screen.findByRole('heading', { name: /Spend by/ });
    expect(window.location.search).toContain(`view=${view}`);
    fireEvent.click(screen.getByRole('button', { name: 'conversation-one' }));
    await waitFor(() => expect(window.location.search).toContain(view === 'chats' ? 'session_id=conversation-one' : view === 'flows' ? 'workflow_id=conversation-one' : 'owner_subject=conversation-one'));
    if (view === 'chats') {
      expect(window.location.search).not.toContain('start=');
      expect(window.location.search).not.toContain('run_id=');
      fireEvent.click(await screen.findByRole('button', { name: 'Back to overview' }));
      await waitFor(() => expect(window.location.search).toContain('view=chats'));
    }
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
