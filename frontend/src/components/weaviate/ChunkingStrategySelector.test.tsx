import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import ChunkingStrategySelector from './ChunkingStrategySelector';

describe('ChunkingStrategySelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const defaultStrategy = {
    strategyName: 'general' as const,
    chunkingMethod: 'by_paragraph' as const,
    maxCharacters: 1500,
    overlapCharacters: 200,
    includeMetadata: true,
    excludeElementTypes: ['Footer', 'Header'],
  };

  it('renders strategy preset selector', () => {
    render(<ChunkingStrategySelector />);

    expect(screen.getByLabelText('Strategy Preset')).toBeInTheDocument();
    expect(screen.getByText('General Purpose')).toBeInTheDocument();
  });

  it('displays initial strategy values', () => {
    const customStrategy = {
      ...defaultStrategy,
      strategyName: 'research' as const,
      chunkingMethod: 'by_title' as const,
      maxCharacters: 2000,
      overlapCharacters: 300,
    };

    render(<ChunkingStrategySelector initialStrategy={customStrategy} />);

    expect(screen.getByDisplayValue('research')).toBeInTheDocument();
    expect(screen.getByDisplayValue('by_title')).toBeInTheDocument();
    expect(screen.getByText('Maximum Characters: 2000')).toBeInTheDocument();
    expect(screen.getByText('Overlap Characters: 300')).toBeInTheDocument();
  });

  it('changes strategy preset and applies predefined settings', async () => {
    render(<ChunkingStrategySelector />);

    const strategySelect = screen.getByLabelText('Strategy Preset');
    fireEvent.mouseDown(strategySelect);

    const researchOption = await screen.findByText('Research Papers');
    fireEvent.click(researchOption);

    // Should apply research preset values
    expect(screen.getByDisplayValue('research')).toBeInTheDocument();
    expect(screen.getByDisplayValue('by_title')).toBeInTheDocument();
    expect(screen.getByText('Maximum Characters: 1500')).toBeInTheDocument();
    expect(screen.getByText('Overlap Characters: 200')).toBeInTheDocument();
  });

  it('displays correct descriptions for each strategy', async () => {
    render(<ChunkingStrategySelector />);

    const strategySelect = screen.getByLabelText('Strategy Preset');

    // Test Research strategy
    fireEvent.mouseDown(strategySelect);
    const researchOption = await screen.findByText('Research Papers');
    fireEvent.click(researchOption);

    expect(screen.getByText(/Optimized for academic papers and research documents/))
      .toBeInTheDocument();

    // Test Legal strategy
    fireEvent.mouseDown(strategySelect);
    const legalOption = await screen.findByText('Legal Documents');
    fireEvent.click(legalOption);

    expect(screen.getByText(/Designed for legal documents/)).toBeInTheDocument();

    // Test Technical strategy
    fireEvent.mouseDown(strategySelect);
    const technicalOption = await screen.findByText('Technical Manuals');
    fireEvent.click(technicalOption);

    expect(screen.getByText(/Best for technical manuals and documentation/))
      .toBeInTheDocument();

    // Test General strategy
    fireEvent.mouseDown(strategySelect);
    const generalOption = await screen.findByText('General Purpose');
    fireEvent.click(generalOption);

    expect(screen.getByText(/Balanced approach suitable for most document types/))
      .toBeInTheDocument();
  });

  it('changes chunking method', async () => {
    render(<ChunkingStrategySelector />);

    const methodSelect = screen.getByLabelText('Chunking Method');
    fireEvent.mouseDown(methodSelect);

    const sentenceOption = await screen.findByText('By Sentence');
    fireEvent.click(sentenceOption);

    expect(screen.getByDisplayValue('by_sentence')).toBeInTheDocument();
    expect(screen.getByText('Splits at sentence boundaries for natural breaks'))
      .toBeInTheDocument();
  });

  it('displays descriptions for all chunking methods', async () => {
    render(<ChunkingStrategySelector />);

    const methodSelect = screen.getByLabelText('Chunking Method');

    // Test By Title
    fireEvent.mouseDown(methodSelect);
    const titleOption = await screen.findByText('By Title');
    fireEvent.click(titleOption);
    expect(screen.getByText('Splits at section boundaries, preserving document structure'))
      .toBeInTheDocument();

    // Test By Paragraph
    fireEvent.mouseDown(methodSelect);
    const paragraphOption = await screen.findByText('By Paragraph');
    fireEvent.click(paragraphOption);
    expect(screen.getByText('Maintains paragraph integrity for better context'))
      .toBeInTheDocument();

    // Test By Character
    fireEvent.mouseDown(methodSelect);
    const characterOption = await screen.findByText('By Character');
    fireEvent.click(characterOption);
    expect(screen.getByText('Fixed-size chunks with precise control'))
      .toBeInTheDocument();

    // Test By Sentence
    fireEvent.mouseDown(methodSelect);
    const sentenceOption = await screen.findByText('By Sentence');
    fireEvent.click(sentenceOption);
    expect(screen.getByText('Splits at sentence boundaries for natural breaks'))
      .toBeInTheDocument();
  });

  it('adjusts max characters with slider', () => {
    render(<ChunkingStrategySelector />);

    const sliders = screen.getAllByRole('slider');
    const maxCharSlider = sliders[0];

    fireEvent.change(maxCharSlider, { target: { value: 3000 } });

    expect(screen.getByText('Maximum Characters: 3000')).toBeInTheDocument();
  });

  it('adjusts overlap characters with slider', () => {
    render(<ChunkingStrategySelector />);

    const sliders = screen.getAllByRole('slider');
    const overlapSlider = sliders[1];

    fireEvent.change(overlapSlider, { target: { value: 400 } });

    expect(screen.getByText('Overlap Characters: 400')).toBeInTheDocument();
  });

  it('limits overlap to half of max characters', () => {
    render(<ChunkingStrategySelector />);

    // Set max characters to 2000
    const sliders = screen.getAllByRole('slider');
    const maxCharSlider = sliders[0];
    fireEvent.change(maxCharSlider, { target: { value: 2000 } });

    // Overlap slider should have max value of 1000 (half of 2000)
    const overlapSlider = sliders[1];
    expect(overlapSlider).toHaveAttribute('aria-valuemax', '1000');
  });

  it('displays excluded element types', () => {
    render(<ChunkingStrategySelector />);

    expect(screen.getByText('Excluded Elements: Footer, Header')).toBeInTheDocument();
  });

  it('saves strategy on button click', () => {
    const onSave = vi.fn();
    render(<ChunkingStrategySelector onSave={onSave} />);

    const saveButton = screen.getByRole('button', { name: /save strategy/i });
    fireEvent.click(saveButton);

    expect(onSave).toHaveBeenCalledWith(defaultStrategy);
  });

  it('saves modified strategy', async () => {
    const onSave = vi.fn();
    render(<ChunkingStrategySelector onSave={onSave} />);

    // Change to research strategy
    const strategySelect = screen.getByLabelText('Strategy Preset');
    fireEvent.mouseDown(strategySelect);
    const researchOption = await screen.findByText('Research Papers');
    fireEvent.click(researchOption);

    // Modify max characters
    const sliders = screen.getAllByRole('slider');
    const maxCharSlider = sliders[0];
    fireEvent.change(maxCharSlider, { target: { value: 3000 } });

    const saveButton = screen.getByRole('button', { name: /save strategy/i });
    fireEvent.click(saveButton);

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        strategyName: 'research',
        chunkingMethod: 'by_title',
        maxCharacters: 3000,
      })
    );
  });

  it('applies correct preset values for legal strategy', async () => {
    render(<ChunkingStrategySelector />);

    const strategySelect = screen.getByLabelText('Strategy Preset');
    fireEvent.mouseDown(strategySelect);

    const legalOption = await screen.findByText('Legal Documents');
    fireEvent.click(legalOption);

    expect(screen.getByDisplayValue('legal')).toBeInTheDocument();
    expect(screen.getByDisplayValue('by_paragraph')).toBeInTheDocument();
    expect(screen.getByText('Maximum Characters: 1000')).toBeInTheDocument();
    expect(screen.getByText('Overlap Characters: 100')).toBeInTheDocument();
  });

  it('applies correct preset values for technical strategy', async () => {
    render(<ChunkingStrategySelector />);

    const strategySelect = screen.getByLabelText('Strategy Preset');
    fireEvent.mouseDown(strategySelect);

    const technicalOption = await screen.findByText('Technical Manuals');
    fireEvent.click(technicalOption);

    expect(screen.getByDisplayValue('technical')).toBeInTheDocument();
    expect(screen.getByDisplayValue('by_character')).toBeInTheDocument();
    expect(screen.getByText('Maximum Characters: 2000')).toBeInTheDocument();
    expect(screen.getByText('Overlap Characters: 400')).toBeInTheDocument();
  });

  it('retains manual changes when switching methods', async () => {
    render(<ChunkingStrategySelector />);

    // Set custom max characters
    const sliders = screen.getAllByRole('slider');
    const maxCharSlider = sliders[0];
    fireEvent.change(maxCharSlider, { target: { value: 3500 } });

    // Change chunking method
    const methodSelect = screen.getByLabelText('Chunking Method');
    fireEvent.mouseDown(methodSelect);
    const sentenceOption = await screen.findByText('By Sentence');
    fireEvent.click(sentenceOption);

    // Max characters should remain the same
    expect(screen.getByText('Maximum Characters: 3500')).toBeInTheDocument();
  });

  it('displays info alert with strategy description', () => {
    render(<ChunkingStrategySelector />);

    const alerts = screen.getAllByRole('alert');
    const infoAlert = alerts.find(alert =>
      alert.textContent?.includes('Balanced approach suitable for most document types')
    );

    expect(infoAlert).toBeInTheDocument();
  });

  it('renders Configuration Preview section', () => {
    render(<ChunkingStrategySelector />);

    expect(screen.getByText('Configuration Preview')).toBeInTheDocument();
  });

  it('displays correct helper text for sliders', () => {
    render(<ChunkingStrategySelector />);

    expect(screen.getByText('Maximum size of each chunk in characters'))
      .toBeInTheDocument();
    expect(screen.getByText('Character overlap between consecutive chunks for context preservation'))
      .toBeInTheDocument();
  });

  it('shows slider marks for max characters', () => {
    render(<ChunkingStrategySelector />);

    // Check for slider marks
    expect(screen.getByText('500')).toBeInTheDocument();
    expect(screen.getByText('2500')).toBeInTheDocument();
    expect(screen.getByText('5000')).toBeInTheDocument();
  });

  it('shows dynamic slider marks for overlap', () => {
    render(<ChunkingStrategySelector />);

    // Initially with max 1500, overlap should show marks at 0 and 750
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('750')).toBeInTheDocument();

    // Change max characters
    const sliders = screen.getAllByRole('slider');
    const maxCharSlider = sliders[0];
    fireEvent.change(maxCharSlider, { target: { value: 4000 } });

    // Should now show marks at 0 and 2000
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('2000')).toBeInTheDocument();
  });

  it('maintains includeMetadata flag', () => {
    const onSave = vi.fn();
    render(<ChunkingStrategySelector onSave={onSave} />);

    const saveButton = screen.getByRole('button', { name: /save strategy/i });
    fireEvent.click(saveButton);

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        includeMetadata: true,
      })
    );
  });

  it('preserves excludeElementTypes when changing strategies', async () => {
    const onSave = vi.fn();
    render(<ChunkingStrategySelector onSave={onSave} />);

    // Change strategy
    const strategySelect = screen.getByLabelText('Strategy Preset');
    fireEvent.mouseDown(strategySelect);
    const researchOption = await screen.findByText('Research Papers');
    fireEvent.click(researchOption);

    const saveButton = screen.getByRole('button', { name: /save strategy/i });
    fireEvent.click(saveButton);

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        excludeElementTypes: ['Footer', 'Header'],
      })
    );
  });
});