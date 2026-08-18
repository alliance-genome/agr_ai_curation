import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';
import Dashboard from './Dashboard';

const serviceMocks = vi.hoisted(() => ({
  fetchDocumentList: vi.fn(),
}));

vi.mock('../../services/weaviate', () => ({
  fetchDocumentList: serviceMocks.fetchDocumentList,
}));

describe('Dashboard', () => {
  beforeEach(() => {
    vi.mocked(global.fetch).mockReset();
    serviceMocks.fetchDocumentList.mockReset();
  });

  it('loads the normalized document count through the shared list service', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'healthy',
      checks: { api: 'healthy', weaviate: 'healthy' },
      details: { weaviate: { version: '1.0', nodes: 1, collections: 2 } },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    serviceMocks.fetchDocumentList.mockResolvedValueOnce({
      documents: [],
      total: 42,
      limit: 1,
      offset: 0,
    });

    render(<Dashboard />);

    expect(await screen.findByText('42')).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith('/api/weaviate/health');
    expect(serviceMocks.fetchDocumentList).toHaveBeenCalledWith({ page: 0, pageSize: 1 });
    expect(screen.queryByText('Failed to load dashboard data')).not.toBeInTheDocument();
  });
});
