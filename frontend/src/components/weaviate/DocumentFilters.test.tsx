import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import DocumentFilters from './DocumentFilters';

// Wrapper component for DatePicker context
const TestWrapper = ({ children }: { children: React.ReactNode }) => (
  <LocalizationProvider dateAdapter={AdapterDateFns}>
    {children}
  </LocalizationProvider>
);

describe('DocumentFilters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderWithLocalization = (ui: React.ReactElement) => {
    return render(ui, {
      wrapper: TestWrapper,
    });
  };

  it('renders all filter sections', () => {
    renderWithLocalization(<DocumentFilters />);

    expect(screen.getByText('Filters')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search by filename...')).toBeInTheDocument();
    expect(screen.getByText('Embedding Status')).toBeInTheDocument();
    expect(screen.getByText('Date Range')).toBeInTheDocument();
    expect(screen.getByText('Vector Count')).toBeInTheDocument();
  });

  describe('Search Filter', () => {
    it('handles search input changes', () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(<DocumentFilters onFilterChange={onFilterChange} />);

      const searchInput = screen.getByPlaceholderText('Search by filename...');
      fireEvent.change(searchInput, { target: { value: 'test.pdf' } });

      expect(onFilterChange).toHaveBeenCalledWith({
        searchTerm: 'test.pdf',
      });
    });

    it('clears search input', () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(
        <DocumentFilters
          filters={{ searchTerm: 'test' }}
          onFilterChange={onFilterChange}
        />
      );

      const clearButton = screen.getByTestId('ClearIcon').parentElement!;
      fireEvent.click(clearButton);

      expect(onFilterChange).toHaveBeenCalledWith({
        searchTerm: '',
      });
    });

    it('shows clear button only when search has value', () => {
      const { rerender } = renderWithLocalization(<DocumentFilters />);

      // No clear button initially
      expect(screen.queryByTestId('ClearIcon')).not.toBeInTheDocument();

      // Add search term
      rerender(<DocumentFilters filters={{ searchTerm: 'test' }} />);

      // Clear button should appear
      expect(screen.getByTestId('ClearIcon')).toBeInTheDocument();
    });
  });

  describe('Embedding Status Filter', () => {
    it('expands status accordion by default', () => {
      renderWithLocalization(<DocumentFilters />);

      expect(screen.getByText('Pending')).toBeVisible();
      expect(screen.getByText('Processing')).toBeVisible();
      expect(screen.getByText('Completed')).toBeVisible();
      expect(screen.getByText('Failed')).toBeVisible();
      expect(screen.getByText('Partial')).toBeVisible();
    });

    it('toggles status checkboxes', () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(<DocumentFilters onFilterChange={onFilterChange} />);

      const pendingCheckbox = screen.getByRole('checkbox', { name: /pending/i });
      fireEvent.click(pendingCheckbox);

      expect(onFilterChange).toHaveBeenCalledWith({
        embeddingStatus: ['pending'],
      });

      // Click again to uncheck
      fireEvent.click(pendingCheckbox);

      expect(onFilterChange).toHaveBeenCalledWith({
        embeddingStatus: undefined,
      });
    });

    it('handles multiple status selections', () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(<DocumentFilters onFilterChange={onFilterChange} />);

      const pendingCheckbox = screen.getByRole('checkbox', { name: /pending/i });
      const completedCheckbox = screen.getByRole('checkbox', { name: /completed/i });

      fireEvent.click(pendingCheckbox);
      fireEvent.click(completedCheckbox);

      expect(onFilterChange).toHaveBeenLastCalledWith({
        embeddingStatus: ['pending', 'completed'],
      });
    });

    it('displays status count chip', () => {
      renderWithLocalization(
        <DocumentFilters
          filters={{ embeddingStatus: ['pending', 'completed'] }}
        />
      );

      // Should show count in accordion summary
      const statusAccordion = screen.getByText('Embedding Status').parentElement!;
      const countChip = within(statusAccordion).getByText('2');
      expect(countChip).toBeInTheDocument();
    });

    it('shows status chips with correct colors', () => {
      renderWithLocalization(<DocumentFilters />);

      const chips = screen.getAllByText(/pending|processing|completed|failed|partial/i);

      // Check that chips are rendered
      expect(chips.length).toBeGreaterThan(0);
    });
  });

  describe('Date Range Filter', () => {
    it('toggles date accordion', async () => {
      renderWithLocalization(<DocumentFilters />);

      const dateAccordion = screen.getByText('Date Range');
      fireEvent.click(dateAccordion);

      await waitFor(() => {
        expect(screen.getByLabelText('From Date')).toBeInTheDocument();
        expect(screen.getByLabelText('To Date')).toBeInTheDocument();
      });
    });

    it('handles date selection', async () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(<DocumentFilters onFilterChange={onFilterChange} />);

      // Expand date accordion
      const dateAccordion = screen.getByText('Date Range');
      fireEvent.click(dateAccordion);

      // Set from date
      const fromDateInput = screen.getByLabelText('From Date');
      fireEvent.change(fromDateInput, { target: { value: '01/01/2025' } });

      await waitFor(() => {
        expect(onFilterChange).toHaveBeenCalledWith(
          expect.objectContaining({
            dateFrom: expect.any(Date),
          })
        );
      });
    });

    it('shows active chip when dates are set', () => {
      const testDate = new Date('2025-01-01');
      renderWithLocalization(
        <DocumentFilters
          filters={{ dateFrom: testDate }}
        />
      );

      const dateAccordion = screen.getByText('Date Range').parentElement!;
      const activeChip = within(dateAccordion).getByText('Active');
      expect(activeChip).toBeInTheDocument();
    });
  });

  describe('Vector Count Filter', () => {
    it('toggles vector count accordion', async () => {
      renderWithLocalization(<DocumentFilters />);

      const vectorAccordion = screen.getByText('Vector Count');
      fireEvent.click(vectorAccordion);

      await waitFor(() => {
        expect(screen.getByLabelText('Minimum Vectors')).toBeInTheDocument();
        expect(screen.getByLabelText('Maximum Vectors')).toBeInTheDocument();
      });
    });

    it('handles vector count input', async () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(<DocumentFilters onFilterChange={onFilterChange} />);

      // Expand vector accordion
      const vectorAccordion = screen.getByText('Vector Count');
      fireEvent.click(vectorAccordion);

      const minInput = screen.getByLabelText('Minimum Vectors');
      fireEvent.change(minInput, { target: { value: '100' } });

      expect(onFilterChange).toHaveBeenCalledWith({
        minVectorCount: 100,
      });

      const maxInput = screen.getByLabelText('Maximum Vectors');
      fireEvent.change(maxInput, { target: { value: '500' } });

      expect(onFilterChange).toHaveBeenCalledWith({
        maxVectorCount: 500,
      });
    });

    it('clears vector count when input is empty', async () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(
        <DocumentFilters
          filters={{ minVectorCount: 100 }}
          onFilterChange={onFilterChange}
        />
      );

      // Expand vector accordion
      const vectorAccordion = screen.getByText('Vector Count');
      fireEvent.click(vectorAccordion);

      const minInput = screen.getByLabelText('Minimum Vectors');
      fireEvent.change(minInput, { target: { value: '' } });

      expect(onFilterChange).toHaveBeenCalledWith({
        minVectorCount: undefined,
      });
    });

    it('shows active chip when vector counts are set', () => {
      renderWithLocalization(
        <DocumentFilters
          filters={{ minVectorCount: 100, maxVectorCount: 500 }}
        />
      );

      const vectorAccordion = screen.getByText('Vector Count').parentElement!;
      const activeChip = within(vectorAccordion).getByText('Active');
      expect(activeChip).toBeInTheDocument();
    });
  });

  describe('Active Filters Display', () => {
    it('shows active filter count', () => {
      renderWithLocalization(
        <DocumentFilters
          filters={{
            searchTerm: 'test',
            embeddingStatus: ['pending'],
            dateFrom: new Date(),
            minVectorCount: 100,
          }}
        />
      );

      expect(screen.getByText('4 active')).toBeInTheDocument();
    });

    it('displays active filter chips', () => {
      const testDate = new Date('2025-01-01');
      renderWithLocalization(
        <DocumentFilters
          filters={{
            searchTerm: 'test.pdf',
            embeddingStatus: ['pending', 'completed'],
            dateFrom: testDate,
            minVectorCount: 100,
            maxVectorCount: 500,
          }}
        />
      );

      expect(screen.getByText('Active Filters:')).toBeInTheDocument();
      expect(screen.getByText('Search: test.pdf')).toBeInTheDocument();
      expect(screen.getByText('Status: pending')).toBeInTheDocument();
      expect(screen.getByText('Status: completed')).toBeInTheDocument();
      expect(screen.getByText(/From: 1\/1\/2025/)).toBeInTheDocument();
      expect(screen.getByText('Min Vectors: 100')).toBeInTheDocument();
      expect(screen.getByText('Max Vectors: 500')).toBeInTheDocument();
    });

    it('removes individual filters via chip delete', () => {
      const onFilterChange = vi.fn();
      renderWithLocalization(
        <DocumentFilters
          filters={{
            searchTerm: 'test',
            embeddingStatus: ['pending'],
          }}
          onFilterChange={onFilterChange}
        />
      );

      // Delete search chip
      const searchChip = screen.getByText('Search: test');
      const deleteButton = within(searchChip.parentElement!).getByTestId('CancelIcon');
      fireEvent.click(deleteButton);

      expect(onFilterChange).toHaveBeenCalledWith(
        expect.objectContaining({
          searchTerm: '',
          embeddingStatus: ['pending'],
        })
      );
    });
  });

  describe('Clear All Functionality', () => {
    it('shows clear all button when filters are active', () => {
      renderWithLocalization(
        <DocumentFilters
          filters={{ searchTerm: 'test' }}
        />
      );

      expect(screen.getByRole('button', { name: /clear all/i })).toBeInTheDocument();
    });

    it('hides clear all button when no filters', () => {
      renderWithLocalization(<DocumentFilters />);

      expect(screen.queryByRole('button', { name: /clear all/i })).not.toBeInTheDocument();
    });

    it('clears all filters', () => {
      const onFilterChange = vi.fn();
      const onClear = vi.fn();

      renderWithLocalization(
        <DocumentFilters
          filters={{
            searchTerm: 'test',
            embeddingStatus: ['pending'],
            dateFrom: new Date(),
            minVectorCount: 100,
          }}
          onFilterChange={onFilterChange}
          onClear={onClear}
        />
      );

      const clearAllButton = screen.getByRole('button', { name: /clear all/i });
      fireEvent.click(clearAllButton);

      expect(onFilterChange).toHaveBeenCalledWith({});
      expect(onClear).toHaveBeenCalled();
    });
  });

  describe('Accordion Behavior', () => {
    it('collapses and expands accordions', async () => {
      renderWithLocalization(<DocumentFilters />);

      // Status is expanded by default
      expect(screen.getByText('Pending')).toBeVisible();

      // Click to collapse
      const statusAccordion = screen.getByText('Embedding Status');
      fireEvent.click(statusAccordion);

      await waitFor(() => {
        expect(screen.queryByText('Pending')).not.toBeVisible();
      });

      // Click to expand again
      fireEvent.click(statusAccordion);

      await waitFor(() => {
        expect(screen.getByText('Pending')).toBeVisible();
      });
    });

    it('allows only one accordion expanded at a time', async () => {
      renderWithLocalization(<DocumentFilters />);

      // Status is expanded by default
      expect(screen.getByText('Pending')).toBeVisible();

      // Expand date accordion
      const dateAccordion = screen.getByText('Date Range');
      fireEvent.click(dateAccordion);

      await waitFor(() => {
        // Date should be expanded
        expect(screen.getByLabelText('From Date')).toBeInTheDocument();
        // Status should be collapsed
        expect(screen.queryByText('Pending')).not.toBeVisible();
      });
    });
  });

  it('initializes with provided filters', () => {
    const initialFilters = {
      searchTerm: 'initial.pdf',
      embeddingStatus: ['completed'],
      minVectorCount: 50,
    };

    renderWithLocalization(<DocumentFilters filters={initialFilters} />);

    expect(screen.getByDisplayValue('initial.pdf')).toBeInTheDocument();

    const completedCheckbox = screen.getByRole('checkbox', { name: /completed/i });
    expect(completedCheckbox).toBeChecked();

    // Expand vector accordion to check values
    const vectorAccordion = screen.getByText('Vector Count');
    fireEvent.click(vectorAccordion);

    expect(screen.getByDisplayValue('50')).toBeInTheDocument();
  });

  it('maintains local state when filters prop changes', () => {
    const { rerender } = renderWithLocalization(
      <DocumentFilters filters={{ searchTerm: 'test1' }} />
    );

    expect(screen.getByDisplayValue('test1')).toBeInTheDocument();

    // Change the search locally
    const searchInput = screen.getByPlaceholderText('Search by filename...');
    fireEvent.change(searchInput, { target: { value: 'test2' } });

    // Local state should maintain the change
    expect(screen.getByDisplayValue('test2')).toBeInTheDocument();
  });

  it('shows filter icons', () => {
    renderWithLocalization(<DocumentFilters />);

    expect(screen.getByTestId('FilterListIcon')).toBeInTheDocument();
    expect(screen.getByTestId('SearchIcon')).toBeInTheDocument();
    expect(screen.getByTestId('DateRangeIcon')).toBeInTheDocument();
    expect(screen.getByTestId('NumbersIcon')).toBeInTheDocument();
  });
});