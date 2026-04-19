import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ProtectedRoutes } from './App';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { getChatLocalStorageKeys, legacyChatStorageKeys } from './lib/chatCacheKeys';

vi.mock('./services/logger', () => ({
  logger: {
    debug: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
  },
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
  default: () => <div data-testid="weaviate-layout" />,
}));

vi.mock('./components/ForceScrollFix', () => ({
  default: () => null,
}));

vi.mock('./components/MaintenanceBanner', () => ({
  default: () => null,
}));

vi.mock('./components/ConnectionsHealthBanner', () => ({
  default: () => null,
}));

vi.mock('./components/ChangelogDialog', () => ({
  default: () => null,
}));

vi.mock('./pages/HomePage', () => ({ default: () => <div>Home</div> }));
vi.mock('./pages/ViewerSettings', () => ({ default: () => <div>Viewer</div> }));
vi.mock('./pages/AgentStudioPage', () => ({ default: () => <div>Agent Studio</div> }));
vi.mock('./pages/BatchPage', () => ({ default: () => <div>Batch</div> }));
vi.mock('./pages/ChangelogPage', () => ({ default: () => <div>Changelog Page</div> }));
vi.mock('./pages/CurationInventoryPage', () => ({ default: () => <div>Curation Inventory Page</div> }));
vi.mock('./pages/CurationWorkspacePage', () => ({ default: () => <div>Curation Workspace Page</div> }));
vi.mock('./pages/weaviate/Settings', () => ({ default: () => <div>Settings</div> }));
vi.mock('./pages/weaviate/DocumentDetail', () => ({ default: () => <div>Document Detail</div> }));
vi.mock('./pages/weaviate/DocumentsPage', () => ({ default: () => <div>Documents Page</div> }));
vi.mock('./pages/weaviate/Dashboard', () => ({ default: () => <div>Dashboard</div> }));
vi.mock('./pages/weaviate/settings/EmbeddingsSettings', () => ({ default: () => <div>Embeddings</div> }));
vi.mock('./pages/weaviate/settings/DatabaseSettings', () => ({ default: () => <div>Database</div> }));
vi.mock('./pages/weaviate/settings/SchemaSettings', () => ({ default: () => <div>Schema</div> }));
vi.mock('./pages/weaviate/settings/ChunkingSettings', () => ({ default: () => <div>Chunking</div> }));

const theme = createTheme();

const jsonResponse = (payload: unknown, status = 200): Response =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const LogoutHarness = () => {
  const { isAuthenticated, isLoading, logout } = useAuth();

  return (
    <>
      <div data-testid="auth-status">{isLoading ? 'loading' : isAuthenticated ? 'authenticated' : 'anonymous'}</div>
      <button type="button" onClick={() => void logout()}>
        Log Out
      </button>
    </>
  );
};

describe('ProtectedRoutes logout integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();

    delete (window as Window & { location?: Location }).location;
    window.location = {
      href: 'https://app.example.org/',
      reload: vi.fn(),
    } as Location;
  });

  it('suppresses auto-login after AuthContext logout drives the real justLoggedOut flow', async () => {
    const scopedKeys = getChatLocalStorageKeys('user-123');

    localStorage.setItem(legacyChatStorageKeys.messages, '[]');
    localStorage.setItem(legacyChatStorageKeys.sessionId, 'session-123');
    localStorage.setItem(legacyChatStorageKeys.activeDocument, 'doc-123');
    localStorage.setItem(legacyChatStorageKeys.pdfViewerSession, '{"documentId":"doc-123"}');
    localStorage.setItem(legacyChatStorageKeys.userId, 'user-123');

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url === '/api/users/me') {
        return jsonResponse({
          auth_sub: 'user-123',
          email: 'curator@alliancegenome.org',
          display_name: 'Test Curator',
        });
      }

      if (url === '/api/chat/history') {
        return jsonResponse({ total_sessions: 1 });
      }

      if (url === '/api/auth/logout') {
        expect(init).toMatchObject({
          method: 'POST',
          credentials: 'include',
        });

        return jsonResponse({
          status: 'logged_out',
          message: 'User session terminated successfully',
          logout_url: 'https://issuer.example.org/logout',
        });
      }

      throw new Error(`Unexpected fetch call: ${url}`);
    });

    render(
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={['/agent-studio?tab=queued']}>
          <AuthProvider>
            <LogoutHarness />
            <ProtectedRoutes>
              <div>Protected content</div>
            </ProtectedRoutes>
          </AuthProvider>
        </MemoryRouter>
      </ThemeProvider>
    );

    expect(await screen.findByText('Protected content')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Log Out' }));

    await waitFor(() => {
      expect(screen.getByTestId('auth-status')).toHaveTextContent('anonymous');
      expect(screen.queryByText('Protected content')).not.toBeInTheDocument();
    });

    expect(sessionStorage.getItem('justLoggedOut')).toBeNull();
    expect(sessionStorage.getItem('intendedPath')).toBeNull();
    expect(localStorage.getItem(scopedKeys.messages)).toBeNull();
    expect(localStorage.getItem(scopedKeys.sessionId)).toBeNull();
    expect(localStorage.getItem(scopedKeys.activeDocument)).toBeNull();
    expect(localStorage.getItem(scopedKeys.pdfViewerSession)).toBeNull();
    expect(localStorage.getItem(legacyChatStorageKeys.messages)).toBeNull();
    expect(localStorage.getItem(legacyChatStorageKeys.sessionId)).toBeNull();
    expect(localStorage.getItem(legacyChatStorageKeys.activeDocument)).toBeNull();
    expect(localStorage.getItem(legacyChatStorageKeys.pdfViewerSession)).toBeNull();
    expect(localStorage.getItem(legacyChatStorageKeys.userId)).toBeNull();
    expect(window.location.href).toBe('https://issuer.example.org/logout');

    const fetchUrls = vi.mocked(global.fetch).mock.calls.map(([url]) => String(url));
    expect(fetchUrls).toEqual(['/api/users/me', '/api/chat/history', '/api/auth/logout']);
  });
});
