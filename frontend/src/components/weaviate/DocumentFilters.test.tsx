import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import type React from 'react';
import DocumentFilters from './DocumentFilters';

vi.mock('@mui/x-date-pickers/LocalizationProvider', () => ({
  LocalizationProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock('@mui/x-date-pickers/AdapterDateFns', () => ({
  AdapterDateFns: class AdapterDateFns {},
}));

vi.mock('@mui/x-date-pickers/DatePicker', () => ({
  DatePicker: ({
    label,
    value,
    onChange,
  }: {
    label: string;
    value: Date | null;
    onChange: (value: Date | null) => void;
  }) => (
    <input
      aria-label={label}
      value={value ? value.toLocaleDateString() : ''}
      onChange={(event) => {
        const nextValue = event.target.value;
        onChange(nextValue ? new Date(nextValue) : null);
      }}
    />
  ),
}));

describe('DocumentFilters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderFilters = (ui: React.ReactElement) => render(ui);

  it('renders all filter sections', () => {
    renderFilters(<DocumentFilters />);

    expect(screen.getByText('Filters')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search by filename...')).toBeInTheDocument();
    expect(screen.getByText('Embedding Status')).toBeInTheDocument();
    expect(screen.getByText('Date Range')).toBeInTheDocument();
    expect(screen.getByText('Chunk Count')).toBeInTheDocument();
  });

  it('handles search input changes and clear action', () => {
    const onFilterChange = vi.fn();
    renderFilters(<DocumentFilters onFilterChange={onFilterChange} />);

    const searchInput = screen.getByPlaceholderText('Search by filename...');
    fireEvent.change(searchInput, { target: { value: 'test.pdf' } });
    expect(onFilterChange).toHaveBeenCalledWith({ searchTerm: 'test.pdf' });

    const searchInputRoot = searchInput.closest('.MuiInputBase-root');
    expect(searchInputRoot).toBeTruthy();
    if (!searchInputRoot) {
      throw new Error('Unable to find search input root');
    }
    fireEvent.click(within(searchInputRoot).getByRole('button'));
    expect(onFilterChange).toHaveBeenLastCalledWith({ searchTerm: '' });
  });

  it('toggles embedding statuses and supports multiple selections', () => {
    const onFilterChange = vi.fn();
    renderFilters(<DocumentFilters onFilterChange={onFilterChange} />);

    const pendingCheckbox = screen.getByRole('checkbox', { name: /pending/i });
    const completedCheckbox = screen.getByRole('checkbox', { name: /completed/i });

    fireEvent.click(pendingCheckbox);
    expect(onFilterChange).toHaveBeenCalledWith({ embeddingStatus: ['pending'] });

    fireEvent.click(completedCheckbox);
    expect(onFilterChange).toHaveBeenLastCalledWith({ embeddingStatus: ['pending', 'completed'] });

    fireEvent.click(pendingCheckbox);
    expect(onFilterChange).toHaveBeenLastCalledWith({ embeddingStatus: ['completed'] });
  });

  it('shows status selection count chip', () => {
    renderFilters(<DocumentFilters filters={{ embeddingStatus: ['pending', 'completed'] }} />);

    const statusAccordion = screen.getByText('Embedding Status').parentElement!;
    expect(within(statusAccordion).getByText('2')).toBeInTheDocument();
  });

  it('handles date selection when date accordion is expanded', async () => {
    const onFilterChange = vi.fn();
    renderFilters(<DocumentFilters onFilterChange={onFilterChange} />);

    fireEvent.click(screen.getByText('Date Range'));

    const fromDateInput = await screen.findByLabelText('From Date');
    fireEvent.change(fromDateInput, { target: { value: '01/01/2025' } });

    await waitFor(() => {
      expect(onFilterChange).toHaveBeenCalledWith(
        expect.objectContaining({
          dateFrom: expect.any(Date),
        })
      );
    });
  });

  it('handles chunk count input updates', async () => {
    const onFilterChange = vi.fn();
    renderFilters(<DocumentFilters onFilterChange={onFilterChange} />);

    fireEvent.click(screen.getByText('Chunk Count'));

    const minInput = await screen.findByLabelText('Minimum Chunks');
    fireEvent.change(minInput, { target: { value: '100' } });
    expect(onFilterChange).toHaveBeenCalledWith({ minVectorCount: 100 });

    const maxInput = screen.getByLabelText('Maximum Chunks');
    fireEvent.change(maxInput, { target: { value: '500' } });
    expect(onFilterChange).toHaveBeenCalledWith({ minVectorCount: 100, maxVectorCount: 500 });

    fireEvent.change(minInput, { target: { value: '' } });
    expect(onFilterChange).toHaveBeenLastCalledWith({
      minVectorCount: undefined,
      maxVectorCount: 500,
    });
  });

  it('shows active filter summary chips', () => {
    const testDate = new Date('2025-01-01');
    renderFilters(
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

    expect(screen.getByText('4 active')).toBeInTheDocument();
    expect(screen.getByText('Active Filters:')).toBeInTheDocument();
    expect(screen.getByText('Search: test.pdf')).toBeInTheDocument();
    expect(screen.getByText('Status: pending')).toBeInTheDocument();
    expect(screen.getByText('Status: completed')).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`From: ${testDate.toLocaleDateString()}`))).toBeInTheDocument();
    expect(screen.getByText('Min Chunks: 100')).toBeInTheDocument();
    expect(screen.getByText('Max Chunks: 500')).toBeInTheDocument();
  });

  it('clears all filters and calls onClear', () => {
    const onFilterChange = vi.fn();
    const onClear = vi.fn();

    renderFilters(
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

    fireEvent.click(screen.getByRole('button', { name: /clear all/i }));
    expect(onFilterChange).toHaveBeenCalledWith({});
    expect(onClear).toHaveBeenCalled();
  });

  it('allows one accordion open at a time', async () => {
    renderFilters(<DocumentFilters />);

    expect(screen.getByText('Pending')).toBeVisible();

    fireEvent.click(screen.getByText('Date Range'));

    await waitFor(() => {
      expect(screen.getByLabelText('From Date')).toBeInTheDocument();
      expect(screen.queryByText('Pending')).not.toBeVisible();
    });
  });

  it('initializes with provided filters', async () => {
    const initialFilters = {
      searchTerm: 'initial.pdf',
      embeddingStatus: ['completed'],
      minVectorCount: 50,
    };

    renderFilters(<DocumentFilters filters={initialFilters} />);

    expect(screen.getByDisplayValue('initial.pdf')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /completed/i })).toBeChecked();

    fireEvent.click(screen.getByText('Chunk Count'));
    expect(await screen.findByDisplayValue('50')).toBeInTheDocument();
  });

  it('shows filter icons', () => {
    renderFilters(<DocumentFilters />);

    expect(screen.getByTestId('FilterListIcon')).toBeInTheDocument();
    expect(screen.getByTestId('SearchIcon')).toBeInTheDocument();
    expect(screen.getByTestId('DateRangeIcon')).toBeInTheDocument();
    expect(screen.getByTestId('NumbersIcon')).toBeInTheDocument();
  });
});
