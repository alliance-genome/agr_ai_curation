import { createContext, useEffect, useRef, useState } from 'react';
import type { BatchListResponse, BatchLookupResponse, BatchSummary, BatchTerminalResponse } from '../../types/batch';
import { getEnvVar } from '../../utils/env';
import { buildPdfTerminalNotification, type PdfTerminalNotificationJob } from '../documents/pdfTerminalNotifications';
import { PDF_JOB_LIMIT, PDF_JOB_WINDOW_DAYS } from '../documents/documentIntakeConfig';

export interface BatchCounts { running_count: number; pending_documents: number }
export const BatchCountsContext = createContext<BatchCounts | null>(null);
type Notification = { message: string; severity: 'success' | 'info' | 'error' };

export function useGlobalPolling(userId: string | null, pathname: string, publish: (notification: Notification) => void) {
  const current = useRef({ userId, pathname, publish });
  current.current = { userId, pathname, publish };
  const [counts, setCounts] = useState<{ userId: string; value: BatchCounts } | null>(null);

  useEffect(() => {
    setCounts(null);
    if (userId === null) return;
    const intervalMs = Number(getEnvVar('VITE_GLOBAL_POLL_INTERVAL_MS', '10000'));
    const timeoutMs = Number(getEnvVar('VITE_GLOBAL_POLL_TIMEOUT_MS', '15000'));
    const pageSize = Number(getEnvVar('VITE_GLOBAL_POLL_PAGE_SIZE', '20'));
    const overlapMs = Number(getEnvVar('VITE_GLOBAL_POLL_OVERLAP_MS', '60000'));
    if (![intervalMs, timeoutMs, pageSize, overlapMs].every(Number.isSafeInteger)
      || intervalMs <= 0 || timeoutMs <= 0 || pageSize <= 0 || overlapMs < 0) {
      throw new Error('Global polling settings must be positive (overlap may be zero)');
    }
    let alive = true;
    const valid = () => alive && current.current.userId === userId;
    const visible = () => document.visibilityState !== 'hidden';
    let activeCursor: string | null = null;
    let terminalCursor: string | null = null;
    let terminalSince: string | null = null;
    let coverage: BatchTerminalResponse['coverage'] | null = null;
    let seeded = false;
    let pdfSeeded = false;
    const observed = new Set<string>();
    const seen = new Set<string>();
    const pdfSeen = new Set<string>();
    const pending: Array<{ kind: 'batch' | 'pdf'; notification: Notification }> = [];
    const controllers = new Set<AbortController>();
    const busy = new Set<string>();
    const timers = new Set<ReturnType<typeof setTimeout>>();

    const flush = () => {
      if (!valid() || !visible()) return;
      for (const item of pending.splice(0)) {
        const path = current.current.pathname.replace(/\/+$/, '');
        if (item.kind === 'batch' ? path.startsWith('/batch') : path === '/weaviate/add-literature') continue;
        current.current.publish(item.notification);
      }
    };
    const terminal = (batch: BatchSummary, silent: boolean) => {
      observed.delete(batch.id);
      const key = `${batch.id}:${batch.status}`;
      if (seen.has(key)) return;
      seen.add(key);
      if (silent) return;
      const label = batch.flow_name ? ` (${batch.flow_name})` : '';
      pending.push({ kind: 'batch', notification: batch.status === 'cancelled'
        ? { message: `Batch processing cancelled${label}`, severity: 'info' }
        : { message: `Batch completed${label}: ${batch.completed_documents}/${batch.total_documents} succeeded, ${batch.failed_documents} failed`, severity: 'success' } });
      flush();
    };
    // Each category gets one bounded request per tick. Independent task locks
    // isolate PDF and discovery failures from exact counts; no category overlaps.
    const task = <T,>(name: string, url: string, consume: (data: T) => void, body?: object) => {
      if (!valid() || !visible() || busy.has(name)) return;
      busy.add(name);
      const controller = new AbortController();
      controllers.add(controller);
      let expired = false;
      const timer = setTimeout(() => {
        expired = true;
        controller.abort();
        controllers.delete(controller);
        timers.delete(timer);
        busy.delete(name);
      }, timeoutMs);
      timers.add(timer);
      void (async () => {
        try {
          const response = await fetch(url, {
            credentials: 'include', signal: controller.signal,
            ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
          });
          if (!response.ok) throw new Error(`Polling ${name}: HTTP ${response.status}`);
          const data = await response.json() as T;
          if (valid() && !expired) consume(data);
        } catch (error) {
          if (valid() && !expired) console.error(`Global ${name} poll failed:`, error);
        } finally {
          clearTimeout(timer);
          timers.delete(timer);
          controllers.delete(controller);
          if (!expired) busy.delete(name);
        }
      })();
    };
    const tick = () => {
      if (!valid() || !visible()) return;
      flush();
      task<BatchCounts>('counts', '/api/batches/running-count', value => setCounts({ userId, value }));
      const activeQuery = new URLSearchParams({ status: 'active', limit: String(pageSize) });
      if (activeCursor !== null) activeQuery.set('cursor', activeCursor);
      task<BatchListResponse>('active', `/api/batches?${activeQuery}`, data => {
        for (const batch of data.batches) {
          if (!seen.has(`${batch.id}:completed`) && !seen.has(`${batch.id}:cancelled`)) observed.add(batch.id);
        }
        activeCursor = data.next_cursor;
      });
      const terminalQuery = new URLSearchParams({ limit: String(pageSize) });
      if (terminalCursor !== null) terminalQuery.set('cursor', terminalCursor);
      else if (terminalSince !== null) terminalQuery.set('since', terminalSince);
      task<BatchTerminalResponse>('terminals', `/api/batches/terminals?${terminalQuery}`, data => {
        if (data.coverage === 'reset' && coverage !== 'reset') {
          console.info('Global batch terminal coverage reset; unknown older outcomes remain in history.');
        }
        coverage = data.coverage;
        // A reset replaces expired coverage; it does not resolve observed IDs.
        // Initial seeding stays silent even if its continuation had to reset.
        for (const batch of data.batches) terminal(batch, !seeded && !observed.has(batch.id));
        terminalCursor = data.next_cursor;
        if (terminalCursor === null) {
          seeded = true;
          terminalSince = new Date(Math.max(Date.parse(data.window_start), Date.parse(data.window_end) - overlapMs)).toISOString();
        }
      });
      if (!busy.has('lookup') && observed.size > 0) {
        const ids = Array.from(observed).slice(0, pageSize);
        // Rotate even on failure so one failing chunk cannot starve other IDs.
        for (const id of ids) { observed.delete(id); observed.add(id); }
        task<BatchLookupResponse>('lookup', '/api/batches/lookup', data => {
          for (const batch of data.batches) {
            if (batch.status === 'completed' || batch.status === 'cancelled') terminal(batch, false);
          }
          for (const id of data.unavailable_ids) observed.delete(id);
        }, { ids });
      }
      if (current.current.pathname.replace(/\/+$/, '') !== '/weaviate/add-literature') {
        const query = new URLSearchParams({ window_days: String(PDF_JOB_WINDOW_DAYS), limit: String(PDF_JOB_LIMIT), offset: '0' });
        task<{ jobs: PdfTerminalNotificationJob[] }>('PDF', `/api/weaviate/pdf-jobs?${query}`, data => {
          for (const job of data.jobs) {
            const notification = buildPdfTerminalNotification(job);
            if (!notification || pdfSeen.has(notification.key)) continue;
            pdfSeen.add(notification.key);
            if (pdfSeeded) pending.push({ kind: 'pdf', notification });
          }
          pdfSeeded = true;
          flush();
        });
      }
    };
    // Defer mount/resume to coalesce rapid visibility changes and Strict Mode
    // setup/cleanup before starting network work.
    let resume: ReturnType<typeof setTimeout> | undefined;
    const wake = () => {
      clearTimeout(resume);
      if (visible()) resume = setTimeout(tick, 0);
    };
    wake();
    const interval = setInterval(tick, intervalMs);
    document.addEventListener('visibilitychange', wake);
    return () => {
      alive = false;
      clearInterval(interval);
      clearTimeout(resume);
      document.removeEventListener('visibilitychange', wake);
      for (const timer of timers) clearTimeout(timer);
      for (const controller of controllers) controller.abort();
    };
  }, [userId]);
  return counts?.userId === userId ? counts.value : null;
}
