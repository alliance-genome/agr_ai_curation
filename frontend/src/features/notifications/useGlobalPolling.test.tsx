import { StrictMode } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import BatchNavIcon from '../../components/BatchNavIcon';
import { BatchCountsContext, useGlobalPolling } from './useGlobalPolling';

const publish = vi.fn();
function Owner({ uid = 'a', path = '/' }: { uid?: string | null; path?: string }) {
  const counts = useGlobalPolling(uid, path, publish);
  return <MemoryRouter><BatchCountsContext.Provider value={counts}><BatchNavIcon /><output>{JSON.stringify(counts)}</output></BatchCountsContext.Provider></MemoryRouter>;
}
const batch = (id: string, status = 'completed') => ({ id, status, flow_name: 'Flow', completed_documents: 2, total_documents: 3, failed_documents: 1 });
const page = (batches: object[] = [], next_cursor: string | null = null, coverage = 'recent_window') => ({ batches, next_cursor, coverage, window_start: '2026-10-17T00:00:00Z', window_end: '2026-10-17T01:00:00Z' });
const response = (data: object) => new Response(JSON.stringify(data));
const deferred = () => { let resolve!: (r: Response) => void; const promise = new Promise<Response>(r => { resolve = r; }); return { promise, resolve }; };
let handler: (url: string, init?: RequestInit) => Promise<Response>;
let requests: Array<{ url: string; init?: RequestInit }>;
const urls = (category: string) => requests.filter(r => r.url.startsWith(category));
async function tick(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function visibility(value: 'visible' | 'hidden') { await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value }); document.dispatchEvent(new Event('visibilitychange')); }); }
const standard = async (url: string) => response(url.includes('running-count') ? { running_count: 42, pending_documents: 123 } : url.includes('pdf-jobs') ? { jobs: [] } : url.includes('lookup') ? { batches: [], unavailable_ids: [] } : page());
beforeEach(() => {
  vi.useFakeTimers(); publish.mockReset(); requests = [];
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  handler = standard;
  vi.stubGlobal('fetch', vi.fn((input, init) => { const url = String(input); requests.push({ url, init }); return handler(url, init); }));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.unstubAllEnvs(); });

describe('shared global polling', () => {
  it('shares exact counts with the real badge on batch pages and navigation makes no requests', async () => {
    const view = render(<Owner path="/batch" />); await tick();
    expect(screen.getByRole('button', { name: 'Batch processing, 123 documents remaining' })).toBeInTheDocument();
    expect(screen.getByText(/"running_count":42/)).toBeInTheDocument();
    expect(requests).toHaveLength(4);
    view.rerender(<Owner path="/other" />); await tick(); expect(requests).toHaveLength(4);
    await tick(10000); expect(requests).toHaveLength(8);
    expect(urls('/api/batches/running-count')).toHaveLength(2);
    expect(urls('/api/batches?')[0].url).toContain('status=active');
    expect(requests.every(r => !/\/api\/batches\/[^?]+$/.test(r.url) || r.url.endsWith('running-count'))).toBe(true);
  });
  it('starts no hidden requests, coalesces rapid toggles, and cleans Strict Mode owners', async () => {
    await visibility('hidden'); const view = render(<StrictMode><Owner /></StrictMode>);
    await tick(30000); expect(requests).toHaveLength(0);
    await visibility('visible'); await visibility('hidden'); await visibility('visible'); await tick();
    expect(requests).toHaveLength(4);
    view.unmount(); await visibility('visible'); await tick(30000); expect(requests).toHaveLength(4);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('does not overlap slow requests and isolates stalled PDF fetches and ignored aborts', async () => {
    const slow = deferred(); handler = url => url.includes('pdf-jobs') ? slow.promise : standard(url);
    render(<Owner />); await tick(); await tick(10000);
    expect(urls('/api/weaviate/pdf-jobs')).toHaveLength(1); expect(urls('/api/batches/running-count')).toHaveLength(2);
    await tick(10000); expect(urls('/api/weaviate/pdf-jobs')).toHaveLength(2);
    expect(urls('/api/weaviate/pdf-jobs')[0].init?.signal?.aborted).toBe(true);
    await act(async () => slow.resolve(response({ jobs: [] })));
    expect(publish).not.toHaveBeenCalled();
  });
  it('seeds every initial page silently, retries unread pages, and overlaps only completed windows', async () => {
    let n = 0;
    handler = url => url.includes('/terminals') ? Promise.resolve(++n === 2 ? new Response('', { status: 503 }) : response(page([batch(n === 1 ? 'old1' : n === 3 ? 'old2' : 'new')], n === 1 ? 'page2' : null))) : standard(url);
    render(<Owner />); await tick(); await tick(10000); await tick(10000);
    expect(publish).not.toHaveBeenCalled();
    expect(urls('/api/batches/terminals').map(r => new URL(r.url, 'http://test').searchParams.get('cursor'))).toEqual([null, 'page2', 'page2']);
    await tick(10000); await tick(10000);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(urls('/api/batches/terminals')[3].url).toContain('since=2026-10-17T00%3A59%3A00.000Z');
  });
  it('retains active continuation and rotates observed chunks through failures and expired coverage', async () => {
    vi.stubEnv('VITE_GLOBAL_POLL_PAGE_SIZE', '1'); let active = 0; let lookups = 0;
    handler = async (url, init) => {
      if (url.startsWith('/api/batches?')) return response({ ...page([batch(++active === 1 ? 'a' : 'b', 'running')], active === 1 ? 'active2' : null), total: 42 });
      if (url.includes('/terminals')) return response(page([], null, 'reset'));
      if (url.includes('/lookup')) { lookups++; const { ids } = JSON.parse(String(init?.body)); return lookups === 1 ? new Response('', { status: 503 }) : response({ batches: ids[0] === 'a' ? [batch('a', 'cancelled')] : [], unavailable_ids: ids[0] === 'b' ? ['b'] : [] }); }
      return standard(url);
    };
    render(<Owner />); await tick(); await tick(10000); await tick(10000); await tick(10000);
    expect(urls('/api/batches?')[1].url).toContain('cursor=active2');
    expect(urls('/api/batches/lookup').map(r => JSON.parse(String(r.init?.body)).ids)).toEqual([['a'], ['a'], ['b']]);
    expect(publish).toHaveBeenCalledWith({ message: 'Batch processing cancelled (Flow)', severity: 'info' });
  });
  it('queues hidden completion once and suppresses at the current route when publishing', async () => {
    const slow = deferred(); let n = 0;
    handler = url => url.includes('/terminals') && ++n > 1 ? slow.promise : standard(url);
    const view = render(<Owner />); await tick(); await tick(10000); await visibility('hidden');
    await act(async () => slow.resolve(response(page([batch('new')])))); expect(publish).not.toHaveBeenCalled();
    view.rerender(<Owner path="/batch/123" />); await visibility('visible'); await tick(); expect(publish).not.toHaveBeenCalled();
    view.rerender(<Owner path="/other" />); await tick(10000); expect(publish).not.toHaveBeenCalled();
  });
  it('publishes hidden pending completion once on return and preserves last good counts on errors', async () => {
    const slow = deferred(); let n = 0;
    handler = url => url.includes('/terminals') && ++n > 1 ? slow.promise : standard(url);
    render(<Owner />); await tick(); await tick(10000); await visibility('hidden');
    await act(async () => slow.resolve(response(page([batch('new')]))));
    handler = async () => new Response('', { status: 503 });
    await visibility('visible'); await tick(); await tick(10000);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /123 documents remaining/ })).toBeInTheDocument();
  });
  it.each([null, 'b'])('clears identity state and rejects late responses after switch to %s', async uid => {
    const slow = deferred(); let stall = false;
    handler = url => stall ? slow.promise : standard(url);
    const view = render(<Owner />); await tick(); stall = true; await tick(10000);
    view.rerender(<Owner uid={uid} />);
    expect(screen.getByText('null')).toBeInTheDocument();
    await act(async () => slow.resolve(response(page([batch('old-user')]))));
    expect(publish).not.toHaveBeenCalled(); expect(screen.getByText('null')).toBeInTheDocument();
    expect(requests.slice(4).every(r => r.init?.signal?.aborted)).toBe(true);
  });
  it('preserves PDF content and suppresses late PDF responses on the normalized jobs route', async () => {
    const slow = deferred(); let n = 0;
    handler = url => url.includes('pdf-jobs') && ++n > 1 ? slow.promise : standard(url);
    const view = render(<Owner />); await tick(); await tick(10000);
    view.rerender(<Owner path="/weaviate/add-literature/" />);
    await act(async () => slow.resolve(response({ jobs: [{ job_id: 'pdf', status: 'failed', filename: 'paper.pdf' }] })));
    expect(publish).not.toHaveBeenCalled(); await tick(10000); expect(urls('/api/weaviate/pdf-jobs')).toHaveLength(2);
    view.rerender(<Owner />); await tick(10000); expect(publish).not.toHaveBeenCalled();
  });
});

it('reconciles every observed chunk after a hidden interval and removes unavailable IDs', async () => {
  vi.stubEnv('VITE_GLOBAL_POLL_PAGE_SIZE', '1');
  let active = 0; const chunks: string[][] = [];
  handler = async (url, init) => {
    if (url.startsWith('/api/batches?')) return response(page(++active <= 3 ? [batch(String(active), 'running')] : [], active < 3 ? `p${active}` : null));
    if (url.includes('/lookup')) {
      const { ids } = JSON.parse(String(init?.body)); chunks.push(ids);
      return response({ batches: [], unavailable_ids: [] });
    }
    return standard(url);
  };
  render(<Owner />); await tick(); await tick(10000); await tick(10000);
  await visibility('hidden'); const before = requests.length; await tick(100000); expect(requests).toHaveLength(before);
  handler = async (url, init) => {
    if (url.includes('/lookup')) {
      const { ids } = JSON.parse(String(init?.body)); chunks.push(ids);
      return response({ batches: ids[0] === '2' ? [] : [batch(ids[0], 'completed')], unavailable_ids: ids[0] === '2' ? ['2'] : [] });
    }
    return url.includes('/terminals') ? response(page([], null, 'reset')) : standard(url);
  };
  await visibility('visible'); await tick(); await tick(10000); await tick(10000);
  expect(new Set(chunks.slice(-3).flat())).toEqual(new Set(['1', '2', '3']));
  expect(publish).toHaveBeenCalledTimes(2);
  const count = urls('/api/batches/lookup').length; await tick(10000);
  expect(urls('/api/batches/lookup')).toHaveLength(count);
});

it('retains active cursor on failure and rejects expired count responses', async () => {
  let active = 0; let count = 0; const slow = deferred();
  handler = async url => {
    if (url.startsWith('/api/batches?')) return ++active === 2 ? new Response('', { status: 500 }) : response(page([], 'unread'));
    if (url.includes('running-count')) return ++count === 1 ? slow.promise : response({ running_count: 7, pending_documents: 77 });
    return standard(url);
  };
  render(<Owner />); await tick(); await tick(10000); await tick(10000);
  expect(urls('/api/batches?').slice(1).every(r => r.url.includes('cursor=unread'))).toBe(true);
  await act(async () => slow.resolve(response({ running_count: 999, pending_documents: 999 })));
  expect(screen.getByRole('button', { name: /77 documents remaining/ })).toBeInTheDocument();
});

it('consumes late batch results on the current visible batch route without replay', async () => {
  let n = 0; const slow = deferred();
  handler = url => url.includes('/terminals') && ++n > 1 ? slow.promise : standard(url);
  const view = render(<Owner />); await tick(); await tick(10000);
  view.rerender(<Owner path="/batch" />);
  await act(async () => slow.resolve(response(page([batch('new')]))));
  view.rerender(<Owner path="/" />); await tick(10000);
  expect(publish).not.toHaveBeenCalled();
});

it('new user seeds its own history and receives its own counts after ignored old responses', async () => {
  const slow = deferred(); handler = () => slow.promise;
  const view = render(<Owner />); await tick();
  view.rerender(<Owner uid="b" />); handler = standard; await tick();
  await act(async () => slow.resolve(response({ running_count: 999, pending_documents: 999 })));
  expect(screen.getByRole('button', { name: /123 documents remaining/ })).toBeInTheDocument();
  expect(publish).not.toHaveBeenCalled();
});

it('unmount ignores pending terminal responses even when abort is ignored', async () => {
  let n = 0; const slow = deferred();
  handler = url => url.includes('/terminals') && ++n > 1 ? slow.promise : standard(url);
  const view = render(<Owner />); await tick(); await tick(10000); view.unmount();
  await act(async () => slow.resolve(response(page([batch('late')]))));
  expect(publish).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0);
});

it('uses the configured visible cadence without additional navigation fetches', async () => {
  vi.stubEnv('VITE_GLOBAL_POLL_INTERVAL_MS', '2500');
  render(<Owner />); await tick(); await tick(2499); expect(requests).toHaveLength(4);
  await tick(1); expect(requests).toHaveLength(8);
});

it.each(['invalid', '0', '-1', '2.5'])('rejects invalid cadence %s instead of masking configuration errors', value => {
  vi.stubEnv('VITE_GLOBAL_POLL_INTERVAL_MS', value);
  expect(() => render(<Owner />)).toThrow('Global polling settings must be positive');
  expect(requests).toHaveLength(0);
});
