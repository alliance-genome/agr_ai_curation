import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import DocumentList from './DocumentList';
import type { DocumentSummary } from '../../services/weaviate';
import {
  DOCUMENT_LOADING_STORAGE_KEY,
  DOCUMENT_LOAD_START_EVENT,
} from '../../features/documents/documentLoadEvents';

const refetchHealthMock = vi.fn();
const emitGlobalToastMock = vi.fn();
const openCurationWorkspaceMock = vi.fn();
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
  lastAccessedDate: '2024-01-02T00:00:00.000Z',
  processingStatus: 'completed',
  embeddingStatus: 'completed',
  chunkCount: 10,
  vectorCount: 100,
  metadata: {
    pageCount: 5,
    author: 'Test Author',
    title: 'Test Document',
    checksum: 'abc123',
    documentType: 'research',
    lastProcessedStage: 'completed',
  },
  ...overrides,
});

vi.mock('../../lib/globalNotifications', () => ({
  emitGlobalToast: (detail: unknown) => emitGlobalToastMock(detail),
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

vi.mock('@mui/x-data-grid', async () => {
  const React = await vi.importActual<typeof import('react')>('react');

  const DataGrid = ({
    rows = [],
    columns = [],
    checkboxSelection = false,
    rowSelectionModel,
    onRowSelectionModelChange,
    sortModel = [],
    onSortModelChange,
    sortingMode = 'client',
    paginationMode = 'client',
    filterMode = 'client',
    sortingOrder = ['asc', 'desc', null],
    sx,
  }: {
    rows?: any[];
    columns?: any[];
    checkboxSelection?: boolean;
    rowSelectionModel?: string[];
    onRowSelectionModelChange?: (ids: string[]) => void;
    sortModel?: Array<{ field: string; sort?: 'asc' | 'desc' | null }>;
    onSortModelChange?: (model: Array<{ field: string; sort?: 'asc' | 'desc' | null }>) => void;
    sortingMode?: 'client' | 'server';
    paginationMode?: 'client' | 'server';
    filterMode?: 'client' | 'server';
    sortingOrder?: Array<'asc' | 'desc' | null>;
    sx?: Record<string, unknown>;
  }) => {
    const [internalSelection, setInternalSelection] = React.useState<string[]>([]);
    const selectedIds =
      rowSelectionModel !== undefined ? rowSelectionModel.map(String) : internalSelection;
    const activeSort = sortModel[0];
    const sortedRows = React.useMemo(() => {
      if (sortingMode === 'server' || !activeSort?.field || !activeSort.sort) {
        return rows;
      }

      const sortedColumn = columns.find((column: any) => column.field === activeSort.field);
      if (!sortedColumn) {
        return rows;
      }

      const direction = activeSort.sort === 'asc' ? 1 : -1;
      return [...rows].sort((left, right) => {
        const leftValue = left[activeSort.field];
        const rightValue = right[activeSort.field];
        const comparison =
          typeof sortedColumn.sortComparator === 'function'
            ? sortedColumn.sortComparator(leftValue, rightValue)
            : String(leftValue ?? '').localeCompare(String(rightValue ?? ''));

        return comparison * direction;
      });
    }, [activeSort?.field, activeSort?.sort, columns, rows, sortingMode]);

    const setSelection = (ids: string[]) => {
      if (rowSelectionModel === undefined) {
        setInternalSelection(ids);
      }
      onRowSelectionModelChange?.(ids);
    };

    const handleHeaderClick = (field: string) => {
      const currentSort = activeSort?.field === field ? activeSort.sort : null;
      const currentIndex = sortingOrder.findIndex((sort) => sort === currentSort);
      const nextSort = sortingOrder[(currentIndex + 1) % sortingOrder.length];

      onSortModelChange?.(nextSort ? [{ field, sort: nextSort }] : []);
    };

    return (
      <div
        className="MuiDataGrid-root"
        role="grid"
        data-sorting-mode={sortingMode}
        data-pagination-mode={paginationMode}
        data-filter-mode={filterMode}
        style={{
          height: typeof sx?.height === 'string' ? sx.height : undefined,
          minHeight: typeof sx?.minHeight === 'number' ? `${sx.minHeight}px` : undefined,
        }}
      >
        <table>
          <thead>
            <tr>
              {checkboxSelection && (
                <th className="MuiDataGrid-columnHeaderCheckbox">
                  <input type="checkbox" />
                </th>
              )}
              {columns.map((column: any) => (
                <th
                  key={column.field}
                  style={{ minWidth: column.minWidth }}
                  aria-sort={
                    activeSort?.field === column.field && activeSort.sort
                      ? activeSort.sort === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : undefined
                  }
                  onClick={() => column.sortable !== false && handleHeaderClick(column.field)}
                >
                  {column.headerName}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row: any) => (
              <tr key={row.id} className="MuiDataGrid-row" style={{ cursor: 'pointer' }}>
                {checkboxSelection && (
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(String(row.id))}
                      onChange={(event) => {
                        const checked = event.target.checked;
                        const id = String(row.id);
                        if (checked) {
                          setSelection([...selectedIds, id]);
                        } else {
                          setSelection(selectedIds.filter((selectedId) => selectedId !== id));
                        }
                      }}
                    />
                  </td>
                )}
                {columns.map((column: any) => {
                  const rawValue = row[column.field];
                  let content = rawValue;

                  if (typeof column.renderCell === 'function') {
                    content = column.renderCell({ row, value: rawValue, field: column.field });
                  } else if (typeof column.valueFormatter === 'function') {
                    content = column.valueFormatter({ row, value: rawValue, field: column.field });
                  }

                  return <td key={`${row.id}-${column.field}`}>{content}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  return {
    DataGrid,
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
    sessionStorage.clear();
    refetchHealthMock.mockReset();
    usePdfExtractionHealthMock.mockClear();
    emitGlobalToastMock.mockReset();
    openCurationWorkspaceMock.mockReset();
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

  it('displays provider provenance in the source column', () => {
    const docs = [
      createTestDocument({
        id: 'provider-doc',
        filename: 'provider.pdf',
        sourceProvenance: {
          provider: 'abc_literature',
          referenceCurie: 'AGRKB:101',
          referenceId: null,
          sourceFileId: 'source-pdf-1',
          pdfArtifactId: 'source-pdf-1',
          convertedArtifactId: 'converted-md-1',
          externalIds: { pmid: '12345' },
          sourceMd5: 'abc123',
          fileClass: 'converted_merged_nxml',
          fileExtension: 'md',
          artifactStatus: 'ready',
          importStatus: 'imported',
          importedAt: null,
          accessScope: 'restricted',
          accessMods: { mods: ['FB'] },
          viewerMode: 'local_pdf',
        },
      }),
      createTestDocument({ id: 'local-doc', filename: 'local.pdf', sourceProvenance: null }),
    ];

    render(<DocumentList {...defaultProps} documents={docs} totalCount={2} />);

    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Source').closest('th')).toHaveStyle({ minWidth: '280px' });
    expect(screen.getByText('ABC Literature')).toBeInTheDocument();
    expect(screen.getByText('AGRKB:101')).toBeInTheDocument();
    expect(screen.getByText('Local PDF')).toBeInTheDocument();
    expect(screen.getByText('Uploaded PDF')).toBeInTheDocument();
    expect(screen.getByText('ABC Literature').closest('.MuiChip-root')).toHaveStyle({
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

  it('handles pagination changes', async () => {
    const { container } = render(<DocumentList {...defaultProps} totalCount={100} />);

    const grid = container.querySelector('.MuiDataGrid-root');
    expect(grid).toBeInTheDocument();
    expect(grid).toHaveAttribute('data-pagination-mode', 'server');
    expect(grid).toHaveAttribute('data-filter-mode', 'server');
    expect(grid).toHaveAttribute('data-sorting-mode', 'client');
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
      within(screen.getByRole('grid'))
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
      within(screen.getByRole('grid'))
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
    expect(screen.getByText('Accessed')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Vectors')).toBeInTheDocument();
    expect(screen.getByText('Chunks')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
  });

  it('formats dates correctly', () => {
    const creationDate = new Date('2024-01-01T10:00:00');
    const lastAccessedDate = new Date('2024-01-02T15:30:00');
    const doc = createTestDocument({
      id: 'date-doc',
      creationDate: '2024-01-01T10:00:00',
      lastAccessedDate: '2024-01-02T15:30:00',
    });

    render(<DocumentList {...defaultProps} documents={[doc]} />);

    expect(screen.getByText(creationDate.toLocaleDateString())).toBeInTheDocument();
    expect(screen.getByText(lastAccessedDate.toLocaleDateString())).toBeInTheDocument();
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
    const grid = document.querySelector('.MuiDataGrid-root');
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

    const rows = container.querySelectorAll('.MuiDataGrid-row');
    expect(rows.length).toBeGreaterThan(0);

    // DataGrid row style should include pointer cursor
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
      const checkboxColumn = document.querySelector('.MuiDataGrid-columnHeaderCheckbox');
      expect(checkboxColumn).not.toBeInTheDocument();
    });

    it('calls onSelectionChange when rows are selected', async () => {
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

      expect(onSelectionChange).toHaveBeenCalled();
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
      const checkboxColumn = document.querySelector('.MuiDataGrid-columnHeaderCheckbox');
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
      expect(container.querySelector('.MuiDataGrid-root')).toBeInTheDocument();
    });

    it('renders without onTitleUpdate prop', () => {
      const { container } = render(<DocumentList {...defaultProps} />);

      // Component should render successfully without onTitleUpdate
      expect(container.querySelector('.MuiDataGrid-root')).toBeInTheDocument();
    });
  });
});
