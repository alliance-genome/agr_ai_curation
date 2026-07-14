import React from 'react';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { MemoryRouter, Outlet, useLocation } from 'react-router-dom';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

import { AppContent, ProtectedRoutes, queryClient } from './App';
import { ThemeModeProvider, THEME_MODE_STORAGE_KEY } from './contexts/ThemeModeContext';
import { GLOBAL_TOAST_EVENT } from './lib/globalNotifications';
import { CHAT_RUN_TERMINAL_EVENT } from './hooks/useChatStream';
import { POPUP_CHANGELOG_ENTRY } from './content/changelog';

const mockUseAuth = vi.hoisted(() => vi.fn());

vi.mock('./contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock('./config/version', () => ({
  getVersionDisplay: () => 'vtest',
  getFullVersionInfo: () => 'Version test',
}));

vi.mock('./components/LogoutDialog', () => ({
  default: () => null,
}));

vi.mock('./components/weaviate/WeaviateNavIcon', () => ({
  default: () => <div data-testid="weaviate-nav" />,
}));

vi.mock('./components/BatchNavIcon', () => ({
  default: () => <div data-testid="batch-nav" />,
}));

vi.mock('./components/weaviate/WeaviateLayout', () => ({
  default: () => (
    <div data-testid="weaviate-layout">
      <Outlet />
    </div>
  ),
}));

vi.mock('./pages/weaviate/Settings', () => ({ default: () => <div>Settings</div> }));
vi.mock('./pages/weaviate/DocumentDetail', () => ({ default: () => <div>Document Detail</div> }));
vi.mock('./pages/weaviate/DocumentsPage', () => ({ default: () => <div>Documents Page</div> }));
vi.mock('./pages/weaviate/AddLiteraturePage', () => ({ default: () => <div>Add Literature Page</div> }));
vi.mock('./pages/weaviate/Dashboard', () => ({ default: () => <div>Dashboard</div> }));
vi.mock('./pages/weaviate/settings/EmbeddingsSettings', () => ({ default: () => <div>Embeddings</div> }));
vi.mock('./pages/weaviate/settings/DatabaseSettings', () => ({ default: () => <div>Database</div> }));
vi.mock('./pages/weaviate/settings/SchemaSettings', () => ({ default: () => <div>Schema</div> }));
vi.mock('./pages/weaviate/settings/ChunkingSettings', () => ({ default: () => <div>Chunking</div> }));
vi.mock('./pages/HomePage', () => ({
  default: () => {
    const location = useLocation();
    return <div data-testid="home-page">Home{location.search}</div>;
  },
}));
vi.mock('./pages/ViewerSettings', () => ({ default: () => <div>Viewer</div> }));
vi.mock('./pages/AgentStudioPage', () => ({ default: () => <div>Agent Studio</div> }));
vi.mock('./pages/BatchPage', () => ({ default: () => <div>Batch</div> }));
vi.mock('./pages/ChangelogPage', () => ({ default: () => <div>Changelog Page</div> }));
vi.mock('./pages/CurationInventoryPage', () => ({ default: () => <div>Curation Inventory Page</div> }));
vi.mock('./features/history/HistoryPage', () => ({ default: () => <div>History Page</div> }));
vi.mock('./components/pdfViewer/PersistentPdfWorkspaceLayout', () => ({
  default: () => (
    <div data-testid="persistent-pdf-workspace-layout">
      <Outlet />
    </div>
  ),
}));

const renderAppContent = (path = '/') =>
  render(
    <ThemeModeProvider>
      <MemoryRouter initialEntries={[path]}>
        <AppContent />
      </MemoryRouter>
    </ThemeModeProvider>
  );

const renderProtectedRoutes = (path = '/') =>
  render(
    <ThemeModeProvider>
      <MemoryRouter initialEntries={[path]}>
        <ProtectedRoutes>
          <div>Protected content</div>
        </ProtectedRoutes>
      </MemoryRouter>
    </ThemeModeProvider>
  );

const jsonResponse = (payload: unknown): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

describe('AppContent global notifications', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem(`changelog:last-seen:user-1`, POPUP_CHANGELOG_ENTRY!.id);
    mockUseAuth.mockReturnValue({
      user: { uid: 'user-1', name: 'Test User' },
      logout: vi.fn().mockResolvedValue(undefined),
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    queryClient.clear();
    vi.useRealTimers();
  });

  it('shows a global snackbar when a global toast event is emitted', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/agent-studio');

    window.dispatchEvent(
      new CustomEvent(GLOBAL_TOAST_EVENT, {
        detail: { message: 'Global toast arrived', severity: 'success' },
      })
    );

    expect(await screen.findByText('Global toast arrived')).toBeInTheDocument();
  });

  it('shows a chat completion toast with a return action when a run finishes off the chat page', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    const view = renderAppContent('/weaviate/documents');
    expect(await screen.findByText('Documents Page')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(CHAT_RUN_TERMINAL_EVENT, {
          detail: {
            sessionId: 'session-finished',
            runKind: 'chat',
            status: 'completed',
            eventStreamVersion: 7,
          },
        },
      ));
    });
    view.rerender(
      <ThemeModeProvider>
        <MemoryRouter initialEntries={['/weaviate/documents']}>
          <AppContent />
        </MemoryRouter>
      </ThemeModeProvider>
    );

    expect(await screen.findByText('Curation chat finished.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open chat' }));
    expect(await screen.findByTestId('persistent-pdf-workspace-layout')).toBeInTheDocument();
    expect(screen.getByTestId('home-page')).toHaveTextContent('Home?session=session-finished');
  });

  it('shows a flow completion toast with a return action when a flow finishes off the chat page', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    const view = renderAppContent('/weaviate/documents');
    expect(await screen.findByText('Documents Page')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(CHAT_RUN_TERMINAL_EVENT, {
          detail: {
            sessionId: 'flow-session-finished',
            runKind: 'flow',
            status: 'completed',
            eventStreamVersion: 3,
          },
        },
      ));
    });
    view.rerender(
      <ThemeModeProvider>
        <MemoryRouter initialEntries={['/weaviate/documents']}>
          <AppContent />
        </MemoryRouter>
      </ThemeModeProvider>
    );

    expect(await screen.findByText('Curation flow finished.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open flow' }));
    expect(await screen.findByTestId('persistent-pdf-workspace-layout')).toBeInTheDocument();
    expect(screen.getByTestId('home-page')).toHaveTextContent('Home?session=flow-session-finished');
  });

  it('does not show a chat completion toast when the curator is already on the chat page', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    const view = renderAppContent('/');
    expect(await screen.findByTestId('persistent-pdf-workspace-layout')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(CHAT_RUN_TERMINAL_EVENT, {
          detail: {
            sessionId: 'session-visible',
            runKind: 'chat',
            status: 'completed',
            eventStreamVersion: 8,
          },
        },
      ));
    });
    view.rerender(
      <ThemeModeProvider>
        <MemoryRouter initialEntries={['/']}>
          <AppContent />
        </MemoryRouter>
      </ThemeModeProvider>
    );

    await waitFor(() => {
      expect(screen.queryByText('Curation chat finished.')).not.toBeInTheDocument();
    });
  });

  it('does not show a chat completion toast for terminal error events', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    const view = renderAppContent('/weaviate/documents');
    expect(await screen.findByText('Documents Page')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(CHAT_RUN_TERMINAL_EVENT, {
          detail: {
            sessionId: 'session-error',
            runKind: 'chat',
            status: 'error',
            eventStreamVersion: 9,
          },
        },
      ));
    });
    view.rerender(
      <ThemeModeProvider>
        <MemoryRouter initialEntries={['/weaviate/documents']}>
          <AppContent />
        </MemoryRouter>
      </ThemeModeProvider>
    );

    await waitFor(() => {
      expect(screen.queryByText('Curation chat finished.')).not.toBeInTheDocument();
      expect(screen.queryByText('Curation chat finished with errors.')).not.toBeInTheDocument();
    });
  });

  it('shows changelog once per user and persists dismissal', async () => {
    localStorage.removeItem(`changelog:last-seen:user-1`);

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    const firstRender = renderAppContent('/');
    expect(await screen.findByText(`What's New: v${POPUP_CHANGELOG_ENTRY!.version}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => {
      expect(localStorage.getItem('changelog:last-seen:user-1')).toBe(POPUP_CHANGELOG_ENTRY!.id);
    });

    firstRender.unmount();
    renderAppContent('/');
    expect(screen.queryByText(`What's New: v${POPUP_CHANGELOG_ENTRY!.version}`)).not.toBeInTheDocument();
  });

  it('renders the add literature route without the changelog popup', async () => {
    localStorage.removeItem(`changelog:last-seen:user-1`);

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/weaviate/add-literature/');

    expect(await screen.findByText('Add Literature Page')).toBeInTheDocument();
    expect(screen.queryByText(`What's New: v${POPUP_CHANGELOG_ENTRY!.version}`)).not.toBeInTheDocument();
  });

  it('keeps the document import mock route as an add literature alias', async () => {
    localStorage.removeItem(`changelog:last-seen:user-1`);

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/weaviate/documents/import-mock/');

    expect(await screen.findByText('Add Literature Page')).toBeInTheDocument();
    expect(screen.queryByText(`What's New: v${POPUP_CHANGELOG_ENTRY!.version}`)).not.toBeInTheDocument();
  });

  it('uses the v0.8.13 complete saved-flow upgrade notes for the changelog popup', () => {
    expect(POPUP_CHANGELOG_ENTRY?.id).toBe('2026-07-14-v0.8.13');
    expect(POPUP_CHANGELOG_ENTRY?.version).toBe('0.8.13');
  });

  it('seeds existing PDF terminal jobs and only toasts new terminal updates on subsequent polls', async () => {
    vi.useFakeTimers();

    let pdfPoll = 0;
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        pdfPoll += 1;
        if (pdfPoll === 1) {
          return jsonResponse({
            jobs: [{ job_id: 'seed-job', status: 'completed', filename: 'seed.pdf', document_id: 'seed-doc' }],
          });
        }
        return jsonResponse({
          jobs: [
            { job_id: 'seed-job', status: 'completed', filename: 'seed.pdf', document_id: 'seed-doc' },
            { job_id: 'new-job', status: 'completed', filename: 'new.pdf', document_id: 'new-doc' },
          ],
        });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/');

    await act(async () => {
      await Promise.resolve();
    });
    expect(global.fetch).toHaveBeenCalled();
    expect(screen.queryByText('PDF processing completed: seed.pdf')).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10000);
      await Promise.resolve();
    });

    expect(screen.getByText('PDF processing completed: new.pdf')).toBeInTheDocument();
  });

  it('leaves PDF job polling to Add Literature on the add literature route', async () => {
    vi.useFakeTimers();

    let pdfPoll = 0;
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        pdfPoll += 1;
        return jsonResponse({
          jobs: [{ job_id: 'active-add-literature', status: 'completed', filename: 'queued-from-add-literature.pdf', document_id: 'doc-add-lit' }],
        });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/weaviate/add-literature');

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText('Add Literature Page')).toBeInTheDocument();
    expect(pdfPoll).toBe(0);
    expect(screen.queryByText('PDF processing completed: queued-from-add-literature.pdf')).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10000);
      await Promise.resolve();
    });

    expect(pdfPoll).toBe(0);
    expect(screen.queryByText('PDF processing completed: queued-from-add-literature.pdf')).not.toBeInTheDocument();
  });

  it('ignores failed PDF snapshots when cancellation has been requested', async () => {
    vi.useFakeTimers();

    let pdfPoll = 0;
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        pdfPoll += 1;
        if (pdfPoll === 1) {
          return jsonResponse({ jobs: [] });
        }
        return jsonResponse({
          jobs: [
            { job_id: 'cancel-job', status: 'failed', filename: 'cancel-me.pdf', document_id: 'cancel-doc', cancel_requested: true },
            { job_id: 'cancel-job', status: 'cancelled', filename: 'cancel-me.pdf', document_id: 'cancel-doc', cancel_requested: true },
          ],
        });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/');

    await act(async () => {
      await Promise.resolve();
    });
    expect(global.fetch).toHaveBeenCalled();
    expect(screen.queryByText('PDF processing failed: cancel-me.pdf')).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10000);
      await Promise.resolve();
    });

    expect(screen.queryByText('PDF processing failed: cancel-me.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('PDF processing cancelled: cancel-me.pdf')).toBeInTheDocument();
  });

  it('skips PDF job polling on Add Literature and skips batch polling on batch route', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/weaviate/add-literature');
    await waitFor(() => {
      const urls = vi.mocked(global.fetch).mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.includes('/api/weaviate/pdf-jobs'))).toBe(false);
      expect(urls.some((url) => url.includes('/api/batches'))).toBe(true);
    });

    vi.clearAllMocks();

    renderAppContent('/weaviate/documents');
    await waitFor(() => {
      const urls = vi.mocked(global.fetch).mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.includes('/api/weaviate/pdf-jobs'))).toBe(true);
      expect(urls.some((url) => url.includes('/api/batches'))).toBe(true);
    });

    vi.clearAllMocks();

    renderAppContent('/batch');
    await waitFor(() => {
      const urls = vi.mocked(global.fetch).mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.includes('/api/weaviate/pdf-jobs'))).toBe(true);
      expect(urls.some((url) => url.includes('/api/batches'))).toBe(false);
    });
  });

  it('renders the Curation nav link and inventory route', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/curation');

    expect(await screen.findByText('Curation Inventory Page')).toBeInTheDocument();
    expect(screen.getByText('Curation')).toBeInTheDocument();
  });

  it('renders the Chat History nav link and history route', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/history');

    expect(await screen.findByText('History Page')).toBeInTheDocument();
    expect(screen.getByText('Chat History')).toBeInTheDocument();
  });

  it('renders the header theme toggle and persists changes', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    renderAppContent('/');

    const toggle = await screen.findByRole('button', { name: 'Switch to light mode' });
    fireEvent.click(toggle);

    expect(localStorage.getItem(THEME_MODE_STORAGE_KEY)).toBe('light');
    expect(screen.getByRole('button', { name: 'Switch to dark mode' })).toBeInTheDocument();
  });

  it('clears the singleton react-query cache when the authenticated user changes', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/weaviate/pdf-jobs')) {
        return jsonResponse({ jobs: [] });
      }
      if (url.includes('/api/batches')) {
        return jsonResponse({ batches: [] });
      }
      return jsonResponse({});
    });

    const view = renderAppContent('/');
    await waitFor(() => {
      expect(screen.getByText('Test User')).toBeInTheDocument();
    });

    queryClient.setQueryData(['documents'], { id: 'doc-1' });
    expect(queryClient.getQueryData(['documents'])).toEqual({ id: 'doc-1' });

    mockUseAuth.mockReturnValue({
      user: { uid: 'user-2', name: 'Another User' },
      logout: vi.fn().mockResolvedValue(undefined),
      isAuthenticated: true,
    });

    await act(async () => {
      view.rerender(
        <ThemeModeProvider>
          <MemoryRouter initialEntries={['/']}>
            <AppContent />
          </MemoryRouter>
        </ThemeModeProvider>
      );
      await Promise.resolve();
    });

    expect(queryClient.getQueryData(['documents'])).toBeUndefined();
  });
});

describe('ProtectedRoutes logout suppression', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('does not re-trigger login after logout state propagates across renders', async () => {
    const login = vi.fn();

    mockUseAuth.mockReturnValue({
      isAuthenticated: false,
      isLoading: true,
      login,
      logout: vi.fn().mockResolvedValue(undefined),
      user: null,
    });
    sessionStorage.setItem('justLoggedOut', 'true');

    const view = renderProtectedRoutes('/agent-studio?tab=queued');

    await act(async () => {
      await Promise.resolve();
    });

    expect(login).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('justLoggedOut')).toBeNull();

    mockUseAuth.mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      login,
      logout: vi.fn().mockResolvedValue(undefined),
      user: null,
    });

    await act(async () => {
      view.rerender(
        <ThemeModeProvider>
          <MemoryRouter initialEntries={['/agent-studio?tab=queued']}>
            <ProtectedRoutes>
              <div>Protected content</div>
            </ProtectedRoutes>
          </MemoryRouter>
        </ThemeModeProvider>
      );
      await Promise.resolve();
    });

    expect(login).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('intendedPath')).toBeNull();

    const resumedLogin = vi.fn();

    mockUseAuth.mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      login: resumedLogin,
      logout: vi.fn().mockResolvedValue(undefined),
      user: null,
    });

    await act(async () => {
      view.rerender(
        <ThemeModeProvider>
          <MemoryRouter initialEntries={['/agent-studio?tab=queued']}>
            <ProtectedRoutes>
              <div>Protected content</div>
            </ProtectedRoutes>
          </MemoryRouter>
        </ThemeModeProvider>
      );
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(resumedLogin).toHaveBeenCalledTimes(1);
    });
    expect(sessionStorage.getItem('intendedPath')).toBe('/agent-studio?tab=queued');
  });

  it('keeps logout suppression when auth flips from authenticated to unauthenticated', async () => {
    const login = vi.fn();

    mockUseAuth.mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      login,
      logout: vi.fn().mockResolvedValue(undefined),
      user: null,
    });
    sessionStorage.setItem('justLoggedOut', 'true');

    const view = renderProtectedRoutes('/agent-studio?tab=queued');

    await act(async () => {
      await Promise.resolve();
    });

    expect(login).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('justLoggedOut')).toBeNull();

    mockUseAuth.mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      login,
      logout: vi.fn().mockResolvedValue(undefined),
      user: null,
    });

    await act(async () => {
      view.rerender(
        <ThemeModeProvider>
          <MemoryRouter initialEntries={['/agent-studio?tab=queued']}>
            <ProtectedRoutes>
              <div>Protected content</div>
            </ProtectedRoutes>
          </MemoryRouter>
        </ThemeModeProvider>
      );
      await Promise.resolve();
    });

    expect(login).not.toHaveBeenCalled();
  });

  it('still redirects unauthenticated users to login when no logout suppression is active', async () => {
    const login = vi.fn();

    mockUseAuth.mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      login,
      logout: vi.fn().mockResolvedValue(undefined),
      user: null,
    });

    renderProtectedRoutes('/curation?view=mine');

    await waitFor(() => {
      expect(login).toHaveBeenCalledTimes(1);
    });
    expect(sessionStorage.getItem('intendedPath')).toBe('/curation?view=mine');
  });
});
