import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, userEvent, waitFor, within } from '../../test/test-utils';
import DocumentList from './DocumentList';
import type { DocumentSummary } from '../../services/weaviate';
import {
  DOCUMENT_LOADING_STORAGE_KEY,
  DOCUMENT_LOAD_START_EVENT,
} from '../../features/documents/documentLoadEvents';
import { getDocumentTablePreferencesStorageKey } from '../../features/documents/documentTablePreferences';

const refetchHealthMock = vi.fn();
const emitGlobalToastMock = vi.fn();
const openCurationWorkspaceMock = vi.fn();
const useAuthMock = vi.fn(() => ({ user: { uid: 'user-1' } }));
const usePdfExtractionHealthMock = vi.fn((_options?: unknown) => ({
  data: {
    status: 'healthy',
    last_checked: '2026-03-05T00:00:00Z',
  },
  isLoading: false,
  isError: false,
  isFetching: false,
  refetch: refetchHealthMock,
}));

const createTestDocument = (overrides: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: '1',
  filename: 'test-document.pdf',
  fileSize: 1024000,
  creationDate: '2024-01-01T00:00:00.000Z',
  processingStatus: 'completed',
  embeddingStatus: 'completed',
  chunkCount: 10,
  vectorCount: 100,
  ...overrides,
});

vi.mock('../../lib/globalNotifications', () => ({
  emitGlobalToast: (detail: unknown) => emitGlobalToastMock(detail),
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => useAuthMock(),
}));

vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace'
  );

  return {
    ...actual,
    openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
  };
});

vi.mock('../../services/weaviate', async () => {
  const actual = await vi.importActual<typeof import('../../services/weaviate')>('../../services/weaviate');
  return {
    ...actual,
    usePdfExtractionHealth: (options: unknown) => usePdfExtractionHealthMock(options),
  };
});


const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('DocumentList', () => {
  const defaultProps = {
    documents: [
      createTestDocument({ id: '1', filename: 'doc1.pdf' }),
      createTestDocument({ id: '2', filename: 'doc2.pdf', embeddingStatus: 'processing' }),
      createTestDocument({ id: '3', filename: 'doc3.pdf', embeddingStatus: 'failed' }),
    ],
    loading: false,
    totalCount: 3,
    onDelete: vi.fn(),
    onReembed: vi.fn(),
    onRefresh: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    refetchHealthMock.mockReset();
    usePdfExtractionHealthMock.mockClear();
    emitGlobalToastMock.mockReset();
    openCurationWorkspaceMock.mockReset();
    useAuthMock.mockReturnValue({ user: { uid: 'user-1' } });
  });

  it('restores user-scoped column visibility and order after remount', () => {
    const firstRender = render(<DocumentList {...defaultProps} />);

    fireEvent.click(screen.getByRole('button', { name: 'Table layout' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show Title column' }));
    fireEvent.click(screen.getByRole('button', { name: 'Move Source column earlier' }));
    fireEvent.click(screen.getByRole('button', { name: 'Move Source column earlier' }));
    firstRender.unmount();

    render(<DocumentList {...defaultProps} />);

    expect(screen.queryByRole('columnheader', { name: /^Title/ })).not.toBeInTheDocument();
    const headers = screen.getAllByRole('columnheader');
    expect(headers[0]).toHaveTextContent('Source');
  });

  it('supports keyboard column reordering from the layout popover', async () => {
    const user = userEvent.setup();
    render(<DocumentList {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: 'Table layout' }));

    const filenameCheckbox = screen.getByRole('checkbox', { name: 'Show Filename column' });
    expect(filenameCheckbox).toHaveFocus();

    await user.tab();
    const moveLaterButton = screen.getByRole('button', { name: 'Move Filename column later' });
    expect(moveLaterButton).toHaveFocus();
    await user.keyboard('{Enter}');
    await user.keyboard('{Escape}');

    expect(screen.getAllByRole('columnheader')[0]).toHaveTextContent('Title');
  });

  it('persists header drag ordering across remounts', () => {
    const firstRender = render(<DocumentList {...defaultProps} />);
    const filenameDragHandle = screen.getByLabelText('Drag Filename column');
    const sourceHeader = screen.getByRole('columnheader', { name: /^Source/ });
    const dataTransfer = {
      effectAllowed: 'none',
      setData: vi.fn(),
      getData: vi.fn(() => 'filename'),
    };

    fireEvent.dragStart(filenameDragHandle, { dataTransfer });
    fireEvent.dragOver(sourceHeader, { dataTransfer });
    fireEvent.drop(sourceHeader, { dataTransfer });

    expect(screen.getAllByRole('columnheader')[0]).toHaveTextContent('Title');
    firstRender.unmount();
    render(<DocumentList {...defaultProps} />);
    expect(screen.getAllByRole('columnheader')[0]).toHaveTextContent('Title');
  });

  it('persists keyboard column resizing across remounts', () => {
    const firstRender = render(<DocumentList {...defaultProps} />);
    const resizeHandle = screen.getByRole('separator', { name: 'Resize Filename column' });
    expect(resizeHandle).toHaveAttribute('aria-valuenow', '240');

    fireEvent.keyDown(resizeHandle, { key: 'ArrowRight' });

    expect(screen.getByRole('separator', { name: 'Resize Filename column' }))
      .toHaveAttribute('aria-valuenow', '250');
    firstRender.unmount();
    render(<DocumentList {...defaultProps} />);
    expect(screen.getByRole('separator', { name: 'Resize Filename column' }))
      .toHaveAttribute('aria-valuenow', '250');
  });

  it('keeps pointer resizing separate from native header dragging', async () => {
    const firstRender = render(<DocumentList {...defaultProps} />);
    const resizeHandle = screen.getByRole('separator', { name: 'Resize Filename column' });

    expect(resizeHandle.closest('[draggable="true"]')).toBeNull();
    fireEvent.mouseDown(resizeHandle, { clientX: 240, button: 0 });
    fireEvent.mouseMove(document, { clientX: 280 });
    fireEvent.mouseUp(document, { clientX: 280 });

    await waitFor(() => {
      expect(screen.getByRole('separator', { name: 'Resize Filename column' }))
        .toHaveAttribute('aria-valuenow', '280');
    });
    firstRender.unmount();
    render(<DocumentList {...defaultProps} />);
    expect(screen.getByRole('separator', { name: 'Resize Filename column' }))
      .toHaveAttribute('aria-valuenow', '280');
  });

  it('persists compact row density across remounts', () => {
    const firstRender = render(<DocumentList {...defaultProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Table layout' }));
    fireEvent.click(screen.getByRole('button', { name: 'Compact row density' }));
    expect(screen.getByRole('button', { name: 'Compact row density' })).toHaveAttribute('aria-pressed', 'true');

    firstRender.unmount();
    render(<DocumentList {...defaultProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Table layout' }));
    expect(screen.getByRole('button', { name: 'Compact row density' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('does not toggle visibility when a disabled boundary control is clicked', () => {
    render(<DocumentList {...defaultProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Table layout' }));

    const filenameCheckbox = screen.getByRole('checkbox', { name: 'Show Filename column' });
    const disabledMoveButton = screen.getByRole('button', {
      name: 'Move Filename column earlier',
    });
    expect(disabledMoveButton).toBeDisabled();

    fireEvent.click(disabledMoveButton.parentElement as HTMLElement);

    expect(filenameCheckbox).toBeChecked();
    fireEvent.keyDown(screen.getByRole('presentation'), { key: 'Escape' });
    expect(screen.getByRole('columnheader', { name: /^Filename/ })).toBeInTheDocument();
  });

  it('keeps table preferences isolated by authenticated user', () => {
    localStorage.setItem(
      getDocumentTablePreferencesStorageKey('user-1'),
      JSON.stringify({
        version: 1,
        columnVisibilityModel: { title: false },
        columnOrder: ['title', 'filename'],
      }),
    );
    useAuthMock.mockReturnValue({ user: { uid: 'user-2' } });

    render(<DocumentList {...defaultProps} />);

    expect(screen.getByRole('columnheader', { name: /^Title/ })).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader')[0]).toHaveTextContent('Filename');
  });

  it('resets the persisted table layout to current defaults', () => {
    const firstRender = render(<DocumentList {...defaultProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Table layout' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show Title column' }));
    fireEvent.keyDown(screen.getByRole('presentation'), { key: 'Escape' });

    const resetButton = screen.getByRole('button', { name: 'Reset table layout' });
    expect(resetButton).toBeEnabled();
    fireEvent.click(resetButton);

    expect(screen.getByRole('columnheader', { name: /^Title/ })).toBeInTheDocument();
    expect(resetButton).toBeDisabled();
    expect(localStorage.getItem(getDocumentTablePreferencesStorageKey('user-1'))).toBeNull();

    firstRender.unmount();
    render(<DocumentList {...defaultProps} />);
    expect(screen.getByRole('columnheader', { name: /^Title/ })).toBeInTheDocument();
  });

  it('renders document list with all documents', () => {
    render(<DocumentList {...defaultProps} />);

    expect(screen.getByText('doc1.pdf')).toBeInTheDocument();
    expect(screen.getByText('doc2.pdf')).toBeInTheDocument();
    expect(screen.getByText('doc3.pdf')).toBeInTheDocument();
  });

  it('shows durable failed processing state and error instead of stale pending embedding state', () => {
    const failedDocument = createTestDocument({
      id: 'failed-provider-document',
      filename: 'provider-paper.pdf',
      processingStatus: 'failed',
      embeddingStatus: 'pending',
      errorMessage: 'S07: line 170: Table has no separator line',
    });

    render(<DocumentList {...defaultProps} documents={[failedDocument]} totalCount={1} />);

    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.queryByText('pending')).not.toBeInTheDocument();
    expect(screen.getByText('S07: line 170: Table has no separator line')).toBeInTheDocument();
    const deleteButton = screen.getByTestId('DeleteIcon').closest('button');
    expect(deleteButton).not.toBeDisabled();
  });

  it('keeps an embedding failure visible after document processing completed', () => {
    const failedEmbeddingDocument = createTestDocument({
      id: 'failed-embedding-document',
      processingStatus: 'completed',
      embeddingStatus: 'failed',
    });

    render(<DocumentList {...defaultProps} documents={[failedEmbeddingDocument]} totalCount={1} />);

    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.queryByText('completed')).not.toBeInTheDocument();
  });

  it('renders generic provider provenance from presentation metadata', () => {
    const docs = [
      createTestDocument({
        id: 'provider-doc',
        filename: 'provider.pdf',
        sourceProvenance: {
          provider: 'archive_gateway',
          providerMetadata: {
            displayLabel: 'Genome Archive',
            referenceLabelPriority: ['external_ids.catalog', 'reference_curie'],
          },
          referenceCurie: 'ARCHIVE:101',
          referenceId: null,
          sourceFileId: 'source-pdf-1',
          pdfArtifactId: 'source-pdf-1',
          convertedArtifactId: 'converted-md-1',
          externalIds: { catalog: 'CAT-123', index: 'secondary' },
          sourceMd5: 'abc123',
          fileClass: 'converted_merged_nxml',
          fileExtension: 'md',
          artifactStatus: 'ready',
          importStatus: 'imported',
          importedAt: null,
          accessScope: 'restricted',
          accessGroupIds: ['GROUP'],
          viewerMode: 'local_pdf',
        },
      }),
      createTestDocument({ id: 'local-doc', filename: 'local.pdf', sourceProvenance: null }),
    ];

    render(<DocumentList {...defaultProps} documents={docs} totalCount={2} />);

    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Source').closest('th')).toHaveStyle({ minWidth: '280px' });
    expect(screen.getByText('Genome Archive')).toBeInTheDocument();
    expect(screen.getByText('CATALOG: CAT-123')).toBeInTheDocument();
    expect(screen.queryByText('ARCHIVE:101')).not.toBeInTheDocument();
    expect(screen.getByText('Local PDF')).toBeInTheDocument();
    expect(screen.getByText('Uploaded PDF')).toBeInTheDocument();
    expect(screen.getByText('Genome Archive').closest('.MuiChip-root')).toHaveStyle({
      flexShrink: '0',
    });
    expect(screen.getByText('imported').closest('.MuiChip-root')).toHaveStyle({ flexShrink: '0' });
  });

  it('displays loading state', () => {
    render(<DocumentList {...defaultProps} loading={true} />);

    const progressBar = document.querySelector('.MuiLinearProgress-root');
    expect(progressBar).toBeInTheDocument();
  });

  it('formats file sizes correctly', () => {
    const docs = [
      createTestDocument({ id: '11', fileSize: 1024 }),         // 1 KB
      createTestDocument({ id: '12', fileSize: 1048576 }),      // 1 MB
      createTestDocument({ id: '13', fileSize: 1073741824 }),   // 1 GB
    ];

    render(<DocumentList {...defaultProps} documents={docs} />);

    expect(screen.getByText('1 KB')).toBeInTheDocument();
    expect(screen.getByText('1 MB')).toBeInTheDocument();
    expect(screen.getByText('1 GB')).toBeInTheDocument();
  });

  it('displays embedding status with correct colors', () => {
    render(<DocumentList {...defaultProps} />);

    const completedChip = screen.getByText('completed');
    const processingChip = screen.getByText('processing');
    const failedChip = screen.getByText('failed');

    expect(completedChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorSuccess');
    expect(processingChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorPrimary');
    expect(failedChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorError');
  });

  it('opens document detail dialog on view button click', async () => {
    render(<DocumentList {...defaultProps} />);

    // Find the first view button
    const viewButtons = screen.getAllByTestId('VisibilityIcon');
    fireEvent.click(viewButtons[0].parentElement!);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('calls onReembed when refresh button is clicked', () => {
    render(<DocumentList {...defaultProps} />);

    const refreshButtons = screen
      .getAllByTestId('RefreshIcon')
      .filter((icon) => icon.closest('td') !== null);
    fireEvent.click(refreshButtons[0].parentElement!);

    expect(defaultProps.onReembed).toHaveBeenCalledWith('1');
  });

  it('calls onDelete when delete button is clicked', () => {
    render(<DocumentList {...defaultProps} />);

    const deleteButtons = screen
      .getAllByTestId('DeleteIcon')
      .filter((icon) => icon.closest('td') !== null);
    fireEvent.click(deleteButtons[0].parentElement!);

    expect(defaultProps.onDelete).toHaveBeenCalledWith('1');
  });

  it('navigates to Home with document route state for Load for Chat', () => {
    const loadStartListener = vi.fn();
    window.addEventListener(DOCUMENT_LOAD_START_EVENT, loadStartListener);

    render(<DocumentList {...defaultProps} />);

    const loadButtons = screen
      .getAllByTestId('FileOpenIcon')
      .filter((icon) => icon.closest('td') !== null);
    fireEvent.click(loadButtons[0].parentElement!);

    expect(sessionStorage.getItem(DOCUMENT_LOADING_STORAGE_KEY)).toBe('true');
    expect(loadStartListener).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/', {
      state: {
        loadForChatDocument: {
          id: '1',
          filename: 'doc1.pdf',
        },
      },
    });

    window.removeEventListener(DOCUMENT_LOAD_START_EVENT, loadStartListener);
  });

  it('opens Review & Curate from the document action column', async () => {
    openCurationWorkspaceMock.mockResolvedValue('session-1');

    render(
      <DocumentList
        {...defaultProps}
        documents={[createTestDocument({ id: 'doc-review', embeddingStatus: 'completed' })]}
      />
    );

    fireEvent.click(await screen.findByRole('button', { name: /review & curate/i }));

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          documentId: 'doc-review',
        })
      );
    });
  });

  it('always renders Review & Curate button for completed documents', async () => {
    render(
      <DocumentList
        {...defaultProps}
        documents={[createTestDocument({ id: 'doc-without-session', embeddingStatus: 'completed' })]}
      />
    );

    // Button should render immediately without any availability probe
    expect(await screen.findByRole('button', { name: /review & curate/i })).toBeInTheDocument();
  });

  it('disables re-embed button for processing documents', () => {
    render(<DocumentList {...defaultProps} />);

    const refreshButtons = screen
      .getAllByTestId('RefreshIcon')
      .filter((icon) => icon.closest('td') !== null);
    // Second document is processing
    expect(refreshButtons[1].parentElement).toBeDisabled();
  });

  it('forwards controlled pagination, sorting, loading, and total row count to the grid', () => {
    const onPaginationModelChange = vi.fn();
    const onSortModelChange = vi.fn();
    const { container } = render(
      <DocumentList
        {...defaultProps}
        loading
        totalCount={200}
        paginationModel={{ page: 2, pageSize: 50 }}
        onPaginationModelChange={onPaginationModelChange}
        sortModel={[{ field: 'filename', sort: 'asc' }]}
        onSortModelChange={onSortModelChange}
      />
    );

    const grid = container.querySelector('[data-testid="documents-table-root"]');
    expect(grid).toBeInTheDocument();
    expect(grid).toHaveAttribute('data-pagination-mode', 'server');
    expect(grid).toHaveAttribute('data-filter-mode', 'server');
    expect(grid).toHaveAttribute('data-sorting-mode', 'server');
    expect(grid).toHaveAttribute('data-row-count', '200');
    expect(grid).toHaveAttribute('data-loading', 'true');
    expect(grid).toHaveAttribute('data-page', '2');
    expect(grid).toHaveAttribute('data-page-size', '50');

    fireEvent.click(screen.getByRole('button', { name: 'Go to next page' }));
    expect(onPaginationModelChange).toHaveBeenCalledWith({ page: 3, pageSize: 50 });

    fireEvent.click(screen.getByText('Filename'));
    expect(onSortModelChange).toHaveBeenCalledWith([{ field: 'filename', sort: 'desc' }]);
  });

  it('sorts rows by text, number, and date columns and toggles direction', () => {
    const documents = [
      createTestDocument({
        id: 'doc-gamma',
        filename: 'gamma.pdf',
        fileSize: 3000,
        creationDate: '2024-01-03T00:00:00.000Z',
      }),
      createTestDocument({
        id: 'doc-alpha',
        filename: 'alpha.pdf',
        fileSize: 1000,
        creationDate: '2024-01-02T00:00:00.000Z',
      }),
      createTestDocument({
        id: 'doc-beta',
        filename: 'beta.pdf',
        fileSize: 2000,
        creationDate: '2024-01-01T00:00:00.000Z',
      }),
    ];
    const getRenderedFilenames = () =>
      within(screen.getByRole('table', { name: 'Documents table' }))
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[0].textContent);

    render(<DocumentList {...defaultProps} documents={documents} />);

    expect(getRenderedFilenames()).toEqual(['gamma.pdf', 'alpha.pdf', 'beta.pdf']);

    const filenameHeader = screen.getByText('Filename');
    fireEvent.click(filenameHeader);
    expect(getRenderedFilenames()).toEqual(['alpha.pdf', 'beta.pdf', 'gamma.pdf']);
    expect(filenameHeader.closest('th')).toHaveAttribute('aria-sort', 'ascending');

    fireEvent.click(filenameHeader);
    expect(getRenderedFilenames()).toEqual(['gamma.pdf', 'beta.pdf', 'alpha.pdf']);
    expect(filenameHeader.closest('th')).toHaveAttribute('aria-sort', 'descending');

    fireEvent.click(screen.getByText('Size'));
    expect(getRenderedFilenames()).toEqual(['alpha.pdf', 'beta.pdf', 'gamma.pdf']);

    fireEvent.click(screen.getByText('Created'));
    expect(getRenderedFilenames()).toEqual(['beta.pdf', 'alpha.pdf', 'gamma.pdf']);
  });

  it('sorts nullable number and date values as missing values', () => {
    const documents = [
      createTestDocument({
        id: 'doc-missing',
        filename: 'missing-values.pdf',
        fileSize: null,
        creationDate: null,
      }),
      createTestDocument({
        id: 'doc-zero',
        filename: 'zero-values.pdf',
        fileSize: 0,
        creationDate: '1970-01-01T00:00:00.000Z',
      }),
      createTestDocument({
        id: 'doc-current',
        filename: 'current-values.pdf',
        fileSize: 2000,
        creationDate: '2024-01-01T00:00:00.000Z',
      }),
    ];
    const getRenderedFilenames = () =>
      within(screen.getByRole('table', { name: 'Documents table' }))
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[0].textContent);

    render(<DocumentList {...defaultProps} documents={documents} />);

    fireEvent.click(screen.getByText('Size'));
    expect(getRenderedFilenames()).toEqual([
      'zero-values.pdf',
      'current-values.pdf',
      'missing-values.pdf',
    ]);

    fireEvent.click(screen.getByText('Size'));
    expect(getRenderedFilenames()).toEqual([
      'missing-values.pdf',
      'current-values.pdf',
      'zero-values.pdf',
    ]);

    fireEvent.click(screen.getByText('Created'));
    expect(getRenderedFilenames()).toEqual([
      'zero-values.pdf',
      'current-values.pdf',
      'missing-values.pdf',
    ]);

    fireEvent.click(screen.getByText('Created'));
    expect(getRenderedFilenames()).toEqual([
      'missing-values.pdf',
      'current-values.pdf',
      'zero-values.pdf',
    ]);
  });

  it('displays correct column headers', () => {
    render(<DocumentList {...defaultProps} />);

    expect(screen.getByText('Filename')).toBeInTheDocument();
    expect(screen.getByText('Title')).toBeInTheDocument();
    expect(screen.getByText('Size')).toBeInTheDocument();
    expect(screen.getByText('Created')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Vectors')).toBeInTheDocument();
    expect(screen.getByText('Chunks')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
  });

  it('formats dates correctly', () => {
    const creationDate = new Date('2024-01-01T10:00:00');
    const doc = createTestDocument({
      id: 'date-doc',
      creationDate: '2024-01-01T10:00:00',
    });

    render(<DocumentList {...defaultProps} documents={[doc]} />);

    expect(screen.getByText(creationDate.toLocaleDateString())).toBeInTheDocument();
  });

  it('displays vector and chunk counts', () => {
    const doc = createTestDocument({
      id: 'counts-doc',
      vectorCount: 150,
      chunkCount: 25,
    });

    render(<DocumentList {...defaultProps} documents={[doc]} />);

    expect(screen.getByText('150')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
  });

  it('handles empty document list', () => {
    render(<DocumentList {...defaultProps} documents={[]} totalCount={0} />);

    expect(screen.getByText('No documents yet. Upload a PDF to get started.')).toBeInTheDocument();
    const grid = document.querySelector('[data-testid="documents-table-root"]');
    expect(grid).not.toBeInTheDocument();
  });

  it('hides upload controls when rendered as the Library table', () => {
    const { container } = render(
      <DocumentList {...defaultProps} documents={[]} totalCount={0} showUploadControls={false} />
    );

    expect(screen.getByText('No library documents yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'UPLOAD DOCUMENT(S)' })).not.toBeInTheDocument();
    expect(screen.queryByText(/PDF extraction service/i)).not.toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).not.toBeInTheDocument();
    expect(usePdfExtractionHealthMock).toHaveBeenCalledWith({ enabled: false });
  });

  it('allows selecting multiple files for upload', () => {
    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]');

    expect(fileInput).toHaveAttribute('multiple');
  });

  it('blocks selecting more than 10 files for upload', () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = Array.from(
      { length: 11 },
      (_, index) => new File(['test'], `doc-${index + 1}.pdf`, { type: 'application/pdf' })
    );

    fireEvent.change(fileInput, { target: { files } });

    expect(alertSpy).toHaveBeenCalledWith('Please select up to 10 PDF files at a time');
    alertSpy.mockRestore();
  });

  it('uploads two selected PDF files', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes('/api/weaviate/documents/upload')) {
        return new Response(JSON.stringify({ document_id: crypto.randomUUID() }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.includes('/api/weaviate/documents/pdf-extraction-health')) {
        return new Response(JSON.stringify({ status: 'healthy', service_url: 'http://pdfx' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const onRefresh = vi.fn();
    const { container } = render(<DocumentList {...defaultProps} onRefresh={onRefresh} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(['a'], 'doc-a.pdf', { type: 'application/pdf' }),
      new File(['b'], 'doc-b.pdf', { type: 'application/pdf' }),
    ];

    fireEvent.change(fileInput, { target: { files } });

    await waitFor(() => {
      const uploadCalls = fetchSpy.mock.calls.filter(([url]) =>
        String(url).includes('/api/weaviate/documents/upload')
      );
      expect(uploadCalls).toHaveLength(2);
    });

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalled();
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  it('shows one background-processing toast after a successful single upload', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes('/api/weaviate/documents/upload')) {
        return new Response(JSON.stringify({ document_id: crypto.randomUUID() }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.includes('/api/weaviate/documents/pdf-extraction-health')) {
        return new Response(JSON.stringify({ status: 'healthy', service_url: 'http://pdfx' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [new File(['a'], 'doc-a.pdf', { type: 'application/pdf' })];

    fireEvent.change(fileInput, { target: { files } });

    await waitFor(() => {
      expect(emitGlobalToastMock).toHaveBeenCalledTimes(1);
    });

    expect(emitGlobalToastMock).toHaveBeenCalledWith({
      message: 'Your PDFs are processing in the background. You can safely navigate away.',
      severity: 'info',
      autoHideDurationMs: 6000,
      anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
    });

    fetchSpy.mockRestore();
  });

  it('shows one background-processing toast for a multi-file upload initiation event', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes('/api/weaviate/documents/upload')) {
        return new Response(JSON.stringify({ document_id: crypto.randomUUID() }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.includes('/api/weaviate/documents/pdf-extraction-health')) {
        return new Response(JSON.stringify({ status: 'healthy', service_url: 'http://pdfx' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(['a'], 'doc-a.pdf', { type: 'application/pdf' }),
      new File(['b'], 'doc-b.pdf', { type: 'application/pdf' }),
    ];

    fireEvent.change(fileInput, { target: { files } });

    await waitFor(() => {
      const uploadCalls = fetchSpy.mock.calls.filter(([url]) =>
        String(url).includes('/api/weaviate/documents/upload')
      );
      expect(uploadCalls).toHaveLength(2);
    });

    expect(emitGlobalToastMock).toHaveBeenCalledTimes(1);

    fetchSpy.mockRestore();
  });

  it('applies hover effects on rows', () => {
    const { container } = render(<DocumentList {...defaultProps} />);

    const rows = container.querySelectorAll('[data-testid="document-table-row"]');
    expect(rows.length).toBeGreaterThan(0);

    // The migration retains the existing row hover affordance.
    rows.forEach(row => {
      expect(row).toHaveStyle({ cursor: 'pointer' });
    });
  });

  describe('checkbox selection for batch processing', () => {
    it('renders checkboxes when checkboxSelection is enabled', () => {
      const onSelectionChange = vi.fn();
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={true}
          onSelectionChange={onSelectionChange}
        />
      );

      // Check for the checkbox column header (select all checkbox)
      const checkboxes = document.querySelectorAll('input[type="checkbox"]');
      expect(checkboxes.length).toBeGreaterThan(0);
    });

    it('does not render checkboxes when checkboxSelection is false', () => {
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={false}
        />
      );

      // Should not have checkbox column
      const checkboxColumn = screen.queryByRole('checkbox', { name: 'Select all documents on this page' });
      expect(checkboxColumn).not.toBeInTheDocument();
    });

    it('keeps callback-only selection uncontrolled and accumulates selected rows', async () => {
      const onSelectionChange = vi.fn();
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={true}
          onSelectionChange={onSelectionChange}
        />
      );

      // Find and click a row checkbox
      const checkboxes = document.querySelectorAll('input[type="checkbox"]');
      // First checkbox is "select all", subsequent ones are row checkboxes
      const firstRowCheckbox = checkboxes[1];
      fireEvent.click(firstRowCheckbox);

      expect(firstRowCheckbox).toBeChecked();
      expect(onSelectionChange).toHaveBeenLastCalledWith(['1']);

      const secondRowCheckbox = document.querySelectorAll('input[type="checkbox"]')[2];
      fireEvent.click(secondRowCheckbox);

      expect(secondRowCheckbox).toBeChecked();
      expect(onSelectionChange).toHaveBeenLastCalledWith(['1', '2']);
    });

    it('supports controlled selection via selectedIds prop', () => {
      const onSelectionChange = vi.fn();
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={true}
          selectedIds={['1', '2']}
          onSelectionChange={onSelectionChange}
        />
      );

      // Check that selected rows have checked checkboxes
      const checkboxes = document.querySelectorAll('input[type="checkbox"]:checked');
      // Should have at least 2 checked (the selected rows)
      expect(checkboxes.length).toBeGreaterThanOrEqual(2);
    });

    it('defaults checkboxSelection to false', () => {
      render(<DocumentList {...defaultProps} />);

      // Should not have checkbox column when not explicitly enabled
      const checkboxColumn = screen.queryByRole('checkbox', { name: 'Select all documents on this page' });
      expect(checkboxColumn).not.toBeInTheDocument();
    });
  });

  describe('edit document functionality', () => {
    it('accepts onTitleUpdate prop', () => {
      const onTitleUpdate = vi.fn();
      // Should render without errors when onTitleUpdate is provided
      const { container } = render(
        <DocumentList
          {...defaultProps}
          onTitleUpdate={onTitleUpdate}
        />
      );

      // Component should render successfully
      expect(container.querySelector('[data-testid="documents-table-root"]')).toBeInTheDocument();
    });

    it('renders without onTitleUpdate prop', () => {
      const { container } = render(<DocumentList {...defaultProps} />);

      // Component should render successfully without onTitleUpdate
      expect(container.querySelector('[data-testid="documents-table-root"]')).toBeInTheDocument();
    });
  });
});
