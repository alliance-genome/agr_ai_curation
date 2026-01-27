import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '../../test/test-utils';
import DocumentDetail from './DocumentDetail';

const mockNavigate = vi.fn();
const mockParams = { id: 'doc-123' as string | undefined };

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => mockParams,
  };
});

let lastDialogProps: any;
const originalFetch = global.fetch;

const mockDialog = vi.fn((props: any) => {
  lastDialogProps = props;
  return <div data-testid="document-details-dialog" />;
});

vi.mock('../../components/weaviate/DocumentDetailsDialog', () => ({
  __esModule: true,
  default: (props: any) => mockDialog(props),
}));

const mockFetch = vi.fn();

describe('DocumentDetail page', () => {
  beforeEach(() => {
    mockParams.id = 'doc-123';
    mockNavigate.mockReset();
    mockDialog.mockReset();
    lastDialogProps = null;
    mockFetch.mockReset();
    mockFetch.mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({}),
    });
    global.fetch = mockFetch as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('renders the details dialog when an id is present', () => {
    render(<DocumentDetail />);

    expect(screen.getByTestId('document-details-dialog')).toBeInTheDocument();
    expect(lastDialogProps.documentId).toBe('doc-123');
    expect(lastDialogProps.open).toBe(true);
  });

  it('navigates back when dialog close handler is triggered', () => {
    render(<DocumentDetail />);

    expect(lastDialogProps).toBeTruthy();
    lastDialogProps.onClose();

    expect(mockNavigate).toHaveBeenCalledWith('/api/weaviate/documents');
  });

  it('calls delete endpoint when delete handler is invoked', async () => {
    render(<DocumentDetail />);

    await lastDialogProps.onDelete('doc-123');

    expect(mockFetch).toHaveBeenCalledWith('/api/weaviate/documents/doc-123', {
      method: 'DELETE',
    });
  });

  it('calls re-embed endpoint when reembed handler is invoked', async () => {
    render(<DocumentDetail />);

    await lastDialogProps.onReembed('doc-123');

    expect(mockFetch).toHaveBeenCalledWith('/api/weaviate/documents/doc-123/reembed', {
      method: 'POST',
    });
  });

  it('shows an error message when id is missing', () => {
    mockParams.id = undefined;

    render(<DocumentDetail />);

    expect(screen.getByText(/Document identifier was not provided/i)).toBeInTheDocument();
    const backButton = screen.getByRole('button', { name: /back to documents/i });
    fireEvent.click(backButton);
    expect(mockNavigate).toHaveBeenCalledWith('/api/weaviate/documents');
  });
});
