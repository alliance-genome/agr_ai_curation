import { FormEvent, useEffect, useState } from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import LinearProgress from '@mui/material/LinearProgress';
import TextField from '@mui/material/TextField';
import MenuItem from '@mui/material/MenuItem';
import type { CostReport, Totals, Run } from './types';
import './cost.css';

const displayName = (value: string) => value.replaceAll('_', ' ');
const shortId = (value: string) => value.length > 22 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value;
// Monetary strings stay strings: never round or sum them in browser arithmetic.
export const moneyRange = (lower: string | null | undefined, upper: string | null | undefined) =>
  lower == null ? 'Not priced' : lower === upper ? `$${lower}` : `$${lower} – $${upper}`;

function initialQuery() {
  const supplied = new URLSearchParams(window.location.search);
  if (supplied.size > 0) return supplied.toString();
  const end = new Date();
  end.setUTCHours(0, 0, 0, 0);
  end.setUTCDate(end.getUTCDate() + 1);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 7);
  return new URLSearchParams({ start: start.toISOString(), end: end.toISOString() }).toString();
}

function Summary({ totals }: { totals: Totals }) {
  return <section className="cost-summary" aria-label="Cost summary">
    <div><h2>Recorded charges</h2>
      {totals.recorded_charges.length ? totals.recorded_charges.map(charge => <p className="cost-amount" key={`${charge.unit}:${charge.source}`}>
        {charge.amount} {charge.unit}<small>{charge.attempts} requests · {charge.source}</small>
      </p>) : <p className="cost-amount">Not reported</p>}
      <p>{totals.unknown_charge_attempts} of {totals.attempt_count} requests have no recorded charge. Unknown does not mean free.</p>
    </div>
    <div><h2>Independent estimate</h2><p className="cost-amount">{moneyRange(totals.estimates.lower, totals.estimates.upper)}</p>
      <p>{totals.estimates.priced_attempts} of {totals.attempt_count} requests priced in USD. Estimates are not added to recorded charges.</p>
      {totals.estimates.unpriced_attempts > 0 ? <p className="cost-warning">{totals.estimates.unpriced_attempts} unpriced requests — this estimate is incomplete.</p> : null}
    </div>
    <div><h2>Usage coverage</h2><p className="cost-amount">{totals.attempt_count} requests</p>
      <p>{totals.usage.total_tokens.known_total?.toLocaleString() ?? 'Unknown'} reported tokens · {totals.usage.total_tokens.unknown_attempts} requests missing token totals.</p>
      <p>{Object.entries(totals.outcomes).map(([key, value]) => `${value} ${displayName(key)}`).join(' · ')}</p>
    </div>
  </section>;
}

function AgentBreakdown({ runs, onSelect }: { runs: Run[]; onSelect: (run: Run, invocation: string) => void }) {
  return <section aria-label="Agent and step subtotals"><h2>Agents and flow steps</h2>
    <p>Each subtotal includes only that invocation’s own requests, not its children. Parent links reflect recorded execution; missing links are not inferred.</p>
    {runs.map(run => <details key={`${run.run_id}:${run.document_id}:${run.job_id}`}>
      <summary>Run {shortId(run.run_id)} · {run.attempt_count} requests</summary>
      <div className="cost-table-wrap" role="region" aria-label="Agent subtotals" tabIndex={0}>
        <table><thead><tr><th>Agent / step</th><th>Parent invocation</th><th>Own requests</th><th>Own recorded charges</th><th>Own estimated USD</th></tr></thead>
          <tbody>{run.agents.map(group => <tr key={JSON.stringify([group.agent_id, group.node_id, group.agent_revision, group.invocation_id])}>
            <td>{group.agent_name ?? group.agent_id ?? 'Unknown agent'}<small>{group.node_id ? `Flow step: ${group.node_id}` : 'No flow step recorded'}</small>
              <small>Revision: <code>{group.agent_revision ?? 'Unknown'}</code></small><small>Invocation: <code>{group.invocation_id ?? 'Not recorded'}</code></small></td>
            <td>{group.parent_invocation_id ? <code>{group.parent_invocation_id}</code> : 'No parent recorded'}</td>
            <td>{group.attempt_count}{group.invocation_id ? <small><button className="cost-link" onClick={() => onSelect(run, group.invocation_id!)}>Inspect invocation</button></small> : null}</td>
            <td>{group.recorded_charges.length ? group.recorded_charges.map(charge => <p key={`${charge.unit}:${charge.source}`}>{charge.amount} {charge.unit}<small>{charge.source}</small></p>) : 'Unknown'}</td>
            <td>{moneyRange(group.estimates.lower, group.estimates.upper)}<small>{group.estimates.priced_attempts}/{group.attempt_count} priced · {group.unknown_charge_attempts} charges unknown</small></td>
          </tr>)}</tbody></table>
      </div>
    </details>)}
  </section>;
}

export default function CostApp() {
  const [query, setQuery] = useState(initialQuery);
  const [refresh, setRefresh] = useState(0);
  const [report, setReport] = useState<CostReport | null>(null);
  const [error, setError] = useState('');
  const [status, setStatus] = useState(0);
  const [loading, setLoading] = useState(true);
  const parameters = new URLSearchParams(query);
  const detailed = parameters.has('session_id') || parameters.has('run_id');
  function navigate(next: URLSearchParams) {
    window.history.pushState(window.history.state, '', `/cost/?${next}`);
    setQuery(next.toString());
  }
  useEffect(() => {
    const pop = () => setQuery(initialQuery());
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setReport(null);
    fetch(`/api/admin/cost/reports?${query}`, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
      .then(async response => {
        setStatus(response.status);
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(response.status === 401 ? 'Sign in to view cost reports.' : response.status === 403 ? 'Your account does not have admin access.' : typeof body.detail === 'string' ? body.detail : 'Cost report unavailable. Narrow the filters or try again.');
        }
        return response.json();
      }).then((data: CostReport) => { if (!controller.signal.aborted) setReport(data); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, refresh]);
  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const [key, raw] of data.entries()) {
      const value = String(raw).trim();
      if (value) next.set(key, key === 'start' || key === 'end' ? `${value}T00:00:00Z` : value);
    }
    navigate(next);
  }
  function drill(session: string | null, run?: string, invocation?: string) {
    const next = new URLSearchParams();
    if (session) next.set('session_id', session);
    if (run) next.set('run_id', run);
    if (invocation) next.set('invocation_id', invocation);
    if (report?.pricing_snapshot_id) next.set('snapshot_id', report.pricing_snapshot_id);
    const overviewQuery = detailed ? window.history.state?.costOverviewQuery : query;
    window.history.pushState({ costOverviewQuery: overviewQuery }, '', `/cost/?${next}`);
    setQuery(next.toString());
  }
  function returnToOverview() {
    const origin = window.history.state?.costOverviewQuery;
    const restored = typeof origin === 'string' ? new URLSearchParams(origin).toString() : '';
    window.history.pushState(null, '', restored ? `/cost/?${restored}` : '/cost/');
    setQuery(typeof origin === 'string' ? restored : initialQuery());
  }
  function page(direction: number) {
    if (!report) return;
    const next = new URLSearchParams(query);
    next.set('offset', String(Math.max(0, report.pagination.offset + direction * report.pagination.page_size)));
    navigate(next);
  }
  return <><header className="cost-header"><a href="/">AI Curation</a><span>Admin cost explorer</span></header>
    <main className="cost-main" id="main"><div className="cost-title"><div><h1>Cost explorer</h1><p>Follow the spend from conversations, flow runs and background jobs to each request.</p></div>
      <Button variant="outlined" onClick={() => setRefresh(value => value + 1)} disabled={loading}>Refresh report</Button></div>
      <form className="cost-filters" onSubmit={applyFilters} key={query} aria-label="Cost filters">
        <TextField name="start" label="From (UTC)" type="date" defaultValue={parameters.get('start')?.slice(0, 10) ?? ''} slotProps={{ inputLabel: { shrink: true } }} size="small" />
        <TextField name="end" label="Before (UTC, exclusive)" type="date" defaultValue={parameters.get('end')?.slice(0, 10) ?? ''} slotProps={{ inputLabel: { shrink: true } }} size="small" />
        <TextField select name="activity" label="Activity" defaultValue={parameters.get('activity') ?? ''} size="small"><MenuItem value="">All activities</MenuItem><MenuItem value="interactive_chat">Conversations</MenuItem><MenuItem value="extraction_flow">Flow runs</MenuItem><MenuItem value="authoring">Agent Studio</MenuItem><MenuItem value="authoring_suggestion">Studio suggestions</MenuItem><MenuItem value="standalone_validation">Standalone validation</MenuItem><MenuItem value="background">Background jobs</MenuItem></TextField>
        <Button type="submit" variant="contained" disabled={loading}>Apply filters</Button>
        <details className="cost-advanced"><summary>More filters and exact IDs</summary><div className="cost-filter-grid">
          {['provider', 'model', 'agent_id', 'session_id', 'run_id', 'flow_run_id', 'document_id', 'job_id', 'invocation_id', 'snapshot_id'].map(key => <TextField key={key} name={key} label={displayName(key)} defaultValue={parameters.get(key) ?? ''} size="small" />)}
          <p>Use <code>__unknown__</code> to select missing model or agent attribution. Clear both dates for a full recorded conversation or run.</p>
        </div></details>
      </form>
      {loading ? <div role="status"><p>Reading the ledger…</p><LinearProgress /></div> : null}
      {error ? <Alert severity="error" action={status === 401 ? <Button href="/api/auth/login?destination=cost">Sign in</Button> : <Button onClick={() => setRefresh(value => value + 1)}>Retry</Button>}>{error}</Alert> : null}
      {report ? <>
        <div className="cost-scope"><div><strong>{report.scope === 'time_window' ? 'Selected window subtotal' : parameters.has('invocation_id') ? 'Selected invocation subtotal' : 'Full recorded conversation / run'}</strong><p>Deployment: <code>{report.deployment_id}</code> · Read {new Date(report.generated_at).toLocaleString()}</p></div>
          <div className="cost-actions"><a href={`/api/admin/cost/export?${query}&format=json`}>Export JSON</a><a href={`/api/admin/cost/export?${query}&format=csv`}>Export CSV</a></div></div>
        {detailed ? <div className="cost-selection"><p>{parameters.has('session_id') ? <>Conversation <code>{parameters.get('session_id')}</code> · </> : null}Run <code>{parameters.get('run_id') ?? 'All recorded turns'}</code>{parameters.has('invocation_id') ? <> · Invocation <code>{parameters.get('invocation_id')}</code></> : null}</p><Button onClick={returnToOverview}>Back to overview</Button>{parameters.has('invocation_id') ? <Button onClick={() => drill(parameters.get('session_id'), parameters.get('run_id') ?? undefined)}>Show full run</Button> : null}</div> : null}
        <Summary totals={report.totals} />
        <details className="cost-provenance"><summary>Coverage and pricing provenance</summary>
          <p>New requests capture their executing agent and flow step when available. Pricing uses the provider-reported service tier, not the requested tier. Missing tier information appears as a range where supported. These are estimates, not invoice reconciliation.</p>
          <p>Excluded: {report.coverage.excluded.map(displayName).join(', ')}.</p>
          {Object.entries(report.coverage.external_services).map(([service, coverage]) => <p key={service}>{displayName(service)}: {displayName(coverage)}. These costs are not included in the estimate.</p>)}
          <p>Pricing snapshot: <code>{report.pricing_snapshot_id ?? 'Not configured'}</code><br/>Algorithm: <code>{report.valuation_algorithm}</code><br/>Source: {report.pricing_source ?? 'None'}<br/>Catalog captured: {report.pricing_captured_at ?? 'Unknown'}</p>
          {Object.entries(report.totals.estimates.unavailable_reasons).map(([reason, count]) => <p key={reason}>{count} requests: {displayName(reason)}</p>)}
          <p>JSON exports include exact request IDs, fact revision cutoffs and independent valuations. Refresh to read new facts; totals are not live.</p>
        </details>
        {detailed && report.runs.length > 0 ? <AgentBreakdown runs={report.runs} onSelect={(run, invocation) => drill(run.session_id, run.run_id, invocation)} /> : null}
        <h2>{detailed ? 'Measured requests' : 'Conversations, flow runs and jobs'}</h2>
        {report.totals.attempt_count === 0 ? <Alert severity="info">No recorded requests match these filters. Try a wider window or check that runtime accounting is enabled.</Alert> : <div className="cost-table-wrap" role="region" aria-label={detailed ? 'Request breakdown' : 'Run breakdown'} tabIndex={0}>
          {detailed ? <table><thead><tr><th>Request / agent</th><th>Provider / model</th><th>Usage</th><th>Recorded charge</th><th>Estimated USD</th><th>Outcome</th></tr></thead><tbody>{report.requests.map(row => <tr key={row.attempt_id}>
            <td>{row.operation_type === 'rerank' ? 'Reranking API call' : row.agent_name ?? row.agent_id ?? 'Unknown agent'}<small>{row.node_id ? `Flow step: ${row.node_id}` : 'No flow step recorded'}</small>
              {row.operation_type === 'rerank' ? <small>{row.candidate_count ?? 'Unknown'} candidates · {row.pagination_request ? 'Pagination request' : 'Initial request'}. API calls are not billing units.</small> : null}
              <details><summary>Request details</summary><p>Agent ID: <code>{row.agent_id ?? 'Unknown'}</code><br/>Role: {row.agent_role ?? 'Unknown'}<br/>Agent revision: <code>{row.agent_revision ?? 'Unknown'}</code><br/>Request: <code>{row.attempt_id}</code><br/>Fact revision {row.fact_revision}<br/>{new Date(row.created_at).toLocaleString()}</p></details></td>
            <td>{row.provider}<small>{row.model ?? 'Unknown model'}</small><small>Reported tier: {row.effective_service_tier ?? 'Unknown'}</small><small>Requested tier: {row.requested_service_tier ?? 'Not specified'}</small></td><td>{row.usage.total_tokens?.toLocaleString() ?? 'Unknown'} tokens<details><summary>Token buckets</summary>{Object.entries(row.usage).map(([key, value]) => <p key={key}>{displayName(key)}: {value ?? 'Unknown'}</p>)}</details></td>
            <td>{row.recorded_charge ? `${row.recorded_charge.amount} ${row.recorded_charge.unit}` : 'Unknown'}<small>{row.recorded_charge?.source}</small></td>
            <td>{moneyRange(row.estimate.cost, row.estimate.estimated_cost_upper)}<small>{row.estimate.estimate_unavailable_reason ? displayName(row.estimate.estimate_unavailable_reason) : row.estimate.pricing_uncertainty?.map(displayName).join(', ')}</small></td><td>{displayName(row.outcome)}</td>
          </tr>)}</tbody></table> : <table><thead><tr><th>Conversation / turn</th><th>Activity</th><th>Requests</th><th>Estimated USD</th><th>Coverage</th></tr></thead><tbody>{report.runs.map(row => <tr key={`${row.session_id}:${row.run_id}:${row.flow_run_id}`}>
            <td>{row.session_id ? <button className="cost-link" onClick={() => drill(row.session_id)}>{shortId(row.session_id)}</button> : 'No conversation'}<small><button className="cost-link" onClick={() => drill(row.session_id, row.run_id)}>{row.session_id ? 'Turn' : 'Run'} {shortId(row.run_id)}</button></small><small>{new Date(row.started_at).toLocaleString()}</small>{row.document_id ? <small>Document <code>{row.document_id}</code></small> : null}{row.job_id ? <small>Job <code>{row.job_id}</code></small> : null}</td>
            <td>{displayName(row.activity)}{row.flow_run_id ? <small>Flow run {shortId(row.flow_run_id)}</small> : null}</td><td>{row.attempt_count}</td><td>{moneyRange(row.estimates.lower, row.estimates.upper)}</td><td>{row.estimates.priced_attempts}/{row.attempt_count} priced<small>{row.unknown_charge_attempts} charges unknown</small></td>
          </tr>)}</tbody></table>}
        </div>}
        <div className="cost-pagination"><Button disabled={report.pagination.offset === 0} onClick={() => page(-1)}>Previous page</Button><span>{detailed ? report.pagination.request_count : report.pagination.run_count} {detailed ? 'requests' : 'turns'} · Page {Math.floor(report.pagination.offset / report.pagination.page_size) + 1}</span><Button disabled={report.pagination.offset + report.pagination.page_size >= (detailed ? report.pagination.request_count : report.pagination.run_count)} onClick={() => page(1)}>Next page</Button></div>
      </> : null}
      <footer>AI Curation · Read-only accounting · Scientific content is not included.</footer>
    </main></>;
}
