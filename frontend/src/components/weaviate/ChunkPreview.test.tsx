import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '../../test/test-utils';
import ChunkPreview from './ChunkPreview';
import { createMockChunk } from '../../test/test-utils';

describe('ChunkPreview', () => {
  const mockChunks = [
    createMockChunk({
      id: '1',
      chunkIndex: 0,
      content: 'This is the first chunk with some content.',
      elementType: 'Title',
      pageNumber: 1,
      sectionTitle: 'Introduction',
    }),
    createMockChunk({
      id: '2',
      chunkIndex: 1,
      content: 'A'.repeat(400), // Long content for truncation test
      elementType: 'NarrativeText',
      pageNumber: 2,
      metadata: {
        characterCount: 400,
        wordCount: 100,
        hasTable: true,
        hasImage: false,
      },
    }),
    createMockChunk({
      id: '3',
      chunkIndex: 2,
      elementType: 'Table',
      metadata: {
        characterCount: 200,
        wordCount: 50,
        hasTable: true,
        hasImage: true,
      },
    }),
  ];

  it('renders all chunks', () => {
    render(<ChunkPreview chunks={mockChunks} />);

    expect(screen.getByText('Chunk #0')).toBeInTheDocument();
    expect(screen.getByText('Chunk #1')).toBeInTheDocument();
    expect(screen.getByText('Chunk #2')).toBeInTheDocument();
  });

  it('displays chunk content', () => {
    render(<ChunkPreview chunks={mockChunks} />);

    expect(screen.getByText(/This is the first chunk/)).toBeInTheDocument();
  });

  it('truncates long content by default', () => {
    render(<ChunkPreview chunks={mockChunks} maxPreviewLength={50} />);

    // Second chunk should be truncated
    const truncatedContent = screen.getByText(/A+\.\.\./);
    expect(truncatedContent).toBeInTheDocument();
  });

  it('expands and collapses content on button click', () => {
    render(<ChunkPreview chunks={mockChunks} maxPreviewLength={50} />);

    // Find expand button for second chunk (which has long content)
    const expandButtons = screen.getAllByTestId('ExpandMoreIcon');
    fireEvent.click(expandButtons[0].parentElement!);

    // Content should now be fully visible
    expect(screen.getByText('A'.repeat(400))).toBeInTheDocument();

    // Find collapse button
    const collapseButton = screen.getByTestId('ExpandLessIcon');
    fireEvent.click(collapseButton.parentElement!);

    // Content should be truncated again
    expect(screen.getByText(/A+\.\.\./)).toBeInTheDocument();
  });

  it('displays element type badges with correct colors', () => {
    render(<ChunkPreview chunks={mockChunks} />);

    const titleChip = screen.getByText('Title');
    const narrativeChip = screen.getByText('NarrativeText');
    const tableChip = screen.getByText('Table');

    expect(titleChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorPrimary');
    expect(narrativeChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorDefault');
    expect(tableChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorInfo');
  });

  it('shows page numbers', () => {
    render(<ChunkPreview chunks={mockChunks} />);

    expect(screen.getByText('Page 1')).toBeInTheDocument();
    expect(screen.getByText('Page 2')).toBeInTheDocument();
  });

  it('displays section titles when available', () => {
    render(<ChunkPreview chunks={mockChunks} />);

    expect(screen.getByText('• Introduction')).toBeInTheDocument();
  });

  it('shows metadata when showMetadata is true', () => {
    render(<ChunkPreview chunks={mockChunks} showMetadata={true} />);

    expect(screen.getByText(/Characters: 36/)).toBeInTheDocument();
    expect(screen.getByText(/Words: 7/)).toBeInTheDocument();
  });

  it('hides metadata when showMetadata is false', () => {
    render(<ChunkPreview chunks={mockChunks} showMetadata={false} />);

    expect(screen.queryByText(/Characters:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Words:/)).not.toBeInTheDocument();
  });

  it('displays table and image indicators', () => {
    render(<ChunkPreview chunks={mockChunks} showMetadata={true} />);

    // Should have table indicators
    const tableChips = screen.getAllByText('Table');
    expect(tableChips.length).toBeGreaterThan(0);

    // Should have image indicator for chunk 3
    const imageChips = screen.getAllByText('Image');
    expect(imageChips.length).toBeGreaterThan(0);
  });

  it('shows correct icons for element types', () => {
    render(<ChunkPreview chunks={mockChunks} />);

    // Check for various icons
    expect(screen.getByTestId('DescriptionIcon')).toBeInTheDocument();
    expect(screen.getByTestId('TableChartIcon')).toBeInTheDocument();
  });

  it('handles empty chunks array', () => {
    render(<ChunkPreview chunks={[]} />);

    expect(screen.getByText('No chunks available for preview')).toBeInTheDocument();
  });

  it('formats numbers with locale string', () => {
    const chunk = createMockChunk({
      metadata: {
        characterCount: 1234567,
        wordCount: 98765,
        hasTable: false,
        hasImage: false,
      },
    });

    render(<ChunkPreview chunks={[chunk]} showMetadata={true} />);

    // Numbers should be formatted with commas
    expect(screen.getByText(/1,234,567/)).toBeInTheDocument();
    expect(screen.getByText(/98,765/)).toBeInTheDocument();
  });

  it('respects custom maxPreviewLength', () => {
    const longChunk = createMockChunk({
      content: 'x'.repeat(500),
    });

    render(<ChunkPreview chunks={[longChunk]} maxPreviewLength={100} />);

    const content = screen.getByText(/x+\.\.\./);
    expect(content.textContent?.length).toBeLessThan(500);
  });

  it('applies correct styling to expanded content', () => {
    const chunk = createMockChunk({
      content: 'Test content with\nmultiple\nlines',
    });

    render(<ChunkPreview chunks={[chunk]} />);

    const content = screen.getByText(/Test content/);
    expect(content).toHaveStyle({ whiteSpace: 'normal' });
  });

  it('handles chunks without section titles', () => {
    const chunk = createMockChunk({
      sectionTitle: undefined,
    });

    render(<ChunkPreview chunks={[chunk]} />);

    // Should not crash and should show chunk without section title
    expect(screen.getByText('Chunk #0')).toBeInTheDocument();
  });
});