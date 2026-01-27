import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import userEvent from '@testing-library/user-event';
import Settings from './Settings';

describe('Settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const defaultEmbeddingConfig = {
    modelProvider: 'openai' as const,
    modelName: 'text-embedding-3-small',
    dimensions: 1536,
    batchSize: 50,
  };

  const defaultWeaviateSettings = {
    collectionName: 'PDFDocuments',
    schemaVersion: '1.0.0',
    replicationFactor: 1,
    consistency: 'eventual' as const,
    vectorIndexType: 'hnsw' as const,
  };

  it('renders all tabs', () => {
    render(<Settings />);

    expect(screen.getByRole('tab', { name: /embeddings/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /database/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /schema/i })).toBeInTheDocument();
  });

  it('displays embeddings tab by default', () => {
    render(<Settings />);

    expect(screen.getByText('Embedding Model Configuration')).toBeInTheDocument();
    expect(screen.getByText('Chunking Strategy')).toBeInTheDocument();
  });

  it('switches between tabs correctly', async () => {
    render(<Settings />);

    // Click Database tab
    const databaseTab = screen.getByRole('tab', { name: /database/i });
    fireEvent.click(databaseTab);

    await waitFor(() => {
      expect(screen.getByText('Database Configuration')).toBeInTheDocument();
    });

    // Click Schema tab
    const schemaTab = screen.getByRole('tab', { name: /schema/i });
    fireEvent.click(schemaTab);

    await waitFor(() => {
      expect(screen.getByText('Current Schema Information')).toBeInTheDocument();
    });
  });

  describe('Embeddings Tab', () => {
    it('displays current embedding configuration', () => {
      render(
        <Settings
          embeddingConfig={{
            modelProvider: 'cohere',
            modelName: 'embed-english-v3.0',
            dimensions: 1024,
            batchSize: 25,
          }}
        />
      );

      expect(screen.getByDisplayValue('cohere')).toBeInTheDocument();
      expect(screen.getByDisplayValue('embed-english-v3.0')).toBeInTheDocument();
      expect(screen.getByDisplayValue('1024')).toBeInTheDocument();
    });

    it('changes model provider and updates model options', async () => {
      render(<Settings />);

      const providerSelect = screen.getByLabelText('Model Provider');
      fireEvent.mouseDown(providerSelect);

      const cohereOption = await screen.findByText('Cohere');
      fireEvent.click(cohereOption);

      // Open model name dropdown
      const modelSelect = screen.getByLabelText('Model Name');
      fireEvent.mouseDown(modelSelect);

      // Should show Cohere models
      expect(await screen.findByText('Embed English v3.0')).toBeInTheDocument();
      expect(screen.getByText('Embed Multilingual v3.0')).toBeInTheDocument();
    });

    it('updates dimensions when model changes', async () => {
      render(<Settings />);

      const modelSelect = screen.getByLabelText('Model Name');
      fireEvent.mouseDown(modelSelect);

      const largeModel = await screen.findByText('Text Embedding 3 Large');
      fireEvent.click(largeModel);

      await waitFor(() => {
        expect(screen.getByDisplayValue('3072')).toBeInTheDocument();
      });
    });

    it('adjusts batch size with slider', async () => {
      render(<Settings />);

      const slider = screen.getByRole('slider');

      // Simulate sliding to value 75
      fireEvent.change(slider, { target: { value: 75 } });

      expect(screen.getByText('Batch Size: 75')).toBeInTheDocument();
    });

    it('saves embedding configuration', async () => {
      const onSaveEmbedding = vi.fn();

      render(
        <Settings
          onSaveEmbedding={onSaveEmbedding}
          embeddingConfig={defaultEmbeddingConfig}
        />
      );

      const saveButton = screen.getAllByRole('button', { name: /save configuration/i })[0];
      fireEvent.click(saveButton);

      expect(onSaveEmbedding).toHaveBeenCalledWith(defaultEmbeddingConfig);

      // Should show success message
      await waitFor(() => {
        expect(screen.getByText('Embedding configuration saved successfully')).toBeInTheDocument();
      });
    });

    it('resets embedding configuration', () => {
      render(
        <Settings
          embeddingConfig={{
            ...defaultEmbeddingConfig,
            batchSize: 75,
          }}
        />
      );

      // Change a value
      const slider = screen.getByRole('slider');
      fireEvent.change(slider, { target: { value: 25 } });

      // Reset
      const resetButton = screen.getAllByRole('button', { name: /reset/i })[0];
      fireEvent.click(resetButton);

      // Should reset to original value
      expect(screen.getByText('Batch Size: 75')).toBeInTheDocument();
    });
  });

  describe('Database Tab', () => {
    it('displays current database configuration', async () => {
      render(
        <Settings
          weaviateSettings={{
            collectionName: 'TestCollection',
            schemaVersion: '2.0.0',
            replicationFactor: 3,
            consistency: 'quorum',
            vectorIndexType: 'flat',
          }}
        />
      );

      // Switch to database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      await waitFor(() => {
        expect(screen.getByDisplayValue('TestCollection')).toBeInTheDocument();
        expect(screen.getByDisplayValue('2.0.0')).toBeInTheDocument();
        expect(screen.getByDisplayValue('3')).toBeInTheDocument();
        expect(screen.getByDisplayValue('quorum')).toBeInTheDocument();
        expect(screen.getByDisplayValue('flat')).toBeInTheDocument();
      });
    });

    it('updates collection name', async () => {
      render(<Settings />);

      // Switch to database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      const collectionInput = await screen.findByLabelText('Collection Name');
      fireEvent.change(collectionInput, { target: { value: 'NewCollection' } });

      expect(screen.getByDisplayValue('NewCollection')).toBeInTheDocument();
    });

    it('updates consistency level', async () => {
      render(<Settings />);

      // Switch to database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      const consistencySelect = await screen.findByLabelText('Consistency Level');
      fireEvent.mouseDown(consistencySelect);

      const quorumOption = await screen.findByText('Quorum');
      fireEvent.click(quorumOption);

      expect(screen.getByDisplayValue('quorum')).toBeInTheDocument();
    });

    it('updates vector index type', async () => {
      render(<Settings />);

      // Switch to database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      const indexSelect = await screen.findByLabelText('Vector Index Type');
      fireEvent.mouseDown(indexSelect);

      const flatOption = await screen.findByText('Flat');
      fireEvent.click(flatOption);

      expect(screen.getByDisplayValue('flat')).toBeInTheDocument();
    });

    it('saves database configuration', async () => {
      const onSaveWeaviate = vi.fn();

      render(
        <Settings
          onSaveWeaviate={onSaveWeaviate}
          weaviateSettings={defaultWeaviateSettings}
        />
      );

      // Switch to database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      const saveButton = await screen.findByRole('button', { name: /save settings/i });
      fireEvent.click(saveButton);

      expect(onSaveWeaviate).toHaveBeenCalledWith(defaultWeaviateSettings);

      // Should show success message
      await waitFor(() => {
        expect(screen.getByText('Weaviate settings saved successfully')).toBeInTheDocument();
      });
    });

    it('resets database configuration', async () => {
      render(
        <Settings
          weaviateSettings={{
            ...defaultWeaviateSettings,
            collectionName: 'OriginalCollection',
          }}
        />
      );

      // Switch to database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      // Change a value
      const collectionInput = await screen.findByLabelText('Collection Name');
      fireEvent.change(collectionInput, { target: { value: 'ChangedCollection' } });

      // Reset
      const resetButton = screen.getAllByRole('button', { name: /reset/i })[0];
      fireEvent.click(resetButton);

      // Should reset to original value
      expect(screen.getByDisplayValue('OriginalCollection')).toBeInTheDocument();
    });

    it('validates replication factor input', async () => {
      render(<Settings />);

      // Switch to database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      const replicationInput = await screen.findByLabelText('Replication Factor');

      // Should have min and max attributes
      expect(replicationInput).toHaveAttribute('min', '1');
      expect(replicationInput).toHaveAttribute('max', '10');
    });
  });

  describe('Schema Tab', () => {
    it('displays schema information', async () => {
      render(
        <Settings
          embeddingConfig={defaultEmbeddingConfig}
          weaviateSettings={defaultWeaviateSettings}
        />
      );

      // Switch to schema tab
      const schemaTab = screen.getByRole('tab', { name: /schema/i });
      fireEvent.click(schemaTab);

      await waitFor(() => {
        expect(screen.getByText('Current Schema Information')).toBeInTheDocument();
        expect(screen.getByText('PDFDocuments')).toBeInTheDocument();
        expect(screen.getByText('1.0.0')).toBeInTheDocument();
        expect(screen.getByText('HNSW')).toBeInTheDocument();
        expect(screen.getByText('1536')).toBeInTheDocument();
      });
    });

    it('shows development notice', async () => {
      render(<Settings />);

      // Switch to schema tab
      const schemaTab = screen.getByRole('tab', { name: /schema/i });
      fireEvent.click(schemaTab);

      await waitFor(() => {
        expect(screen.getByText(/Schema management features are currently in development/))
          .toBeInTheDocument();
      });
    });
  });

  describe('Model Options', () => {
    it('provides correct OpenAI models', () => {
      render(<Settings />);

      const modelSelect = screen.getByLabelText('Model Name');
      fireEvent.mouseDown(modelSelect);

      expect(screen.getByText('Text Embedding 3 Small')).toBeInTheDocument();
      expect(screen.getByText('Text Embedding 3 Large')).toBeInTheDocument();
      expect(screen.getByText('Ada v2')).toBeInTheDocument();
    });

    it('provides correct Cohere models', async () => {
      render(<Settings />);

      // Switch to Cohere
      const providerSelect = screen.getByLabelText('Model Provider');
      fireEvent.mouseDown(providerSelect);

      const cohereOption = await screen.findByText('Cohere');
      fireEvent.click(cohereOption);

      // Check models
      const modelSelect = screen.getByLabelText('Model Name');
      fireEvent.mouseDown(modelSelect);

      expect(await screen.findByText('Embed English v3.0')).toBeInTheDocument();
      expect(screen.getByText('Embed Multilingual v3.0')).toBeInTheDocument();
    });

    it('provides correct HuggingFace models', async () => {
      render(<Settings />);

      // Switch to HuggingFace
      const providerSelect = screen.getByLabelText('Model Provider');
      fireEvent.mouseDown(providerSelect);

      const huggingfaceOption = await screen.findByText('HuggingFace');
      fireEvent.click(huggingfaceOption);

      // Check models
      const modelSelect = screen.getByLabelText('Model Name');
      fireEvent.mouseDown(modelSelect);

      expect(await screen.findByText('MiniLM L6 v2')).toBeInTheDocument();
      expect(screen.getByText('MPNet Base v2')).toBeInTheDocument();
    });
  });

  describe('Snackbar Notifications', () => {
    it('shows success snackbar for embedding save', async () => {
      const onSaveEmbedding = vi.fn();

      render(<Settings onSaveEmbedding={onSaveEmbedding} />);

      const saveButton = screen.getAllByRole('button', { name: /save configuration/i })[0];
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(screen.getByText('Embedding configuration saved successfully'))
          .toBeInTheDocument();
      });
    });

    it('auto-hides snackbar after timeout', async () => {
      vi.useFakeTimers();
      const onSaveEmbedding = vi.fn();

      render(<Settings onSaveEmbedding={onSaveEmbedding} />);

      const saveButton = screen.getAllByRole('button', { name: /save configuration/i })[0];
      fireEvent.click(saveButton);

      expect(screen.getByText('Embedding configuration saved successfully'))
        .toBeInTheDocument();

      // Fast-forward 3 seconds
      vi.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.queryByText('Embedding configuration saved successfully'))
          .not.toBeInTheDocument();
      });

      vi.useRealTimers();
    });

    it('closes snackbar on close button click', async () => {
      const onSaveEmbedding = vi.fn();

      render(<Settings onSaveEmbedding={onSaveEmbedding} />);

      const saveButton = screen.getAllByRole('button', { name: /save configuration/i })[0];
      fireEvent.click(saveButton);

      const alert = await screen.findByRole('alert');
      const closeButton = within(alert).getByRole('button');
      fireEvent.click(closeButton);

      await waitFor(() => {
        expect(screen.queryByText('Embedding configuration saved successfully'))
          .not.toBeInTheDocument();
      });
    });
  });

  it('renders ChunkingStrategySelector component', () => {
    render(<Settings />);

    expect(screen.getByText('Chunking Strategy')).toBeInTheDocument();
    // The actual ChunkingStrategySelector component should be tested separately
  });

  it('maintains separate state for each configuration', async () => {
    render(<Settings />);

    // Change embedding config
    const slider = screen.getByRole('slider');
    fireEvent.change(slider, { target: { value: 75 } });

    // Switch to database tab
    const databaseTab = screen.getByRole('tab', { name: /database/i });
    fireEvent.click(databaseTab);

    // Change database config
    const collectionInput = await screen.findByLabelText('Collection Name');
    fireEvent.change(collectionInput, { target: { value: 'NewCollection' } });

    // Switch back to embeddings
    const embeddingsTab = screen.getByRole('tab', { name: /embeddings/i });
    fireEvent.click(embeddingsTab);

    // Should maintain changed value
    expect(screen.getByText('Batch Size: 75')).toBeInTheDocument();
  });
});