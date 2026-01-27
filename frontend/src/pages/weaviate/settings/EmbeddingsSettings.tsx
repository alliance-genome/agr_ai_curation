import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Button,
  Alert,
  CircularProgress,
} from '@mui/material';

interface SettingsData {
  embedding?: {
    model_provider: string;
    model_name: string;
    dimensions: number;
    batch_size: number;
  };
  available_models?: Array<{
    provider: string;
    models: Array<{
      name: string;
      dimensions: number;
    }>;
  }>;
}

const EmbeddingsSettings: React.FC = () => {
  const [provider, setProvider] = useState('openai');
  const [model, setModel] = useState('text-embedding-ada-002');
  const [batchSize, setBatchSize] = useState('100');
  const [availableModels, setAvailableModels] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const response = await fetch('/api/weaviate/settings');
        const data: SettingsData = await response.json();

        if (data.embedding) {
          setProvider(data.embedding.model_provider || 'openai');
          setModel(data.embedding.model_name);
          setBatchSize(data.embedding.batch_size.toString());
        }

        if (data.available_models) {
          setAvailableModels(data.available_models);
        }
      } catch (err) {
        setError('Failed to load settings');
        console.error('Error fetching settings:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchSettings();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await fetch('/api/weaviate/settings', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          embedding_config: {
            model_provider: provider,
            model_name: model,
            batch_size: parseInt(batchSize),
            dimensions: getModelDimensions(provider, model),
          },
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to save settings');
      }

      setSuccess('Settings saved successfully');
    } catch (err) {
      setError('Failed to save settings');
      console.error('Error saving settings:', err);
    } finally {
      setSaving(false);
    }
  };

  const getModelDimensions = (provider: string, modelName: string): number => {
    const providerModels = availableModels.find(p => p.provider === provider);
    const modelInfo = providerModels?.models.find((m: any) => m.name === modelName);
    return modelInfo?.dimensions || 1536;
  };

  const getModelsForProvider = (selectedProvider: string) => {
    const providerModels = availableModels.find(p => p.provider === selectedProvider);
    return providerModels?.models || [];
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Embeddings Settings
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>
          {success}
        </Alert>
      )}

      <Paper sx={{ p: 3, mt: 3 }}>
        <Typography variant="h6" gutterBottom>
          Embedding Model Configuration
        </Typography>

        <FormControl fullWidth sx={{ mt: 2 }}>
          <InputLabel>Provider</InputLabel>
          <Select
            value={provider}
            label="Provider"
            onChange={(e) => {
              setProvider(e.target.value);
              // Reset model when provider changes
              const models = getModelsForProvider(e.target.value);
              if (models.length > 0) {
                setModel(models[0].name);
              }
            }}
          >
            {availableModels.map((p) => (
              <MenuItem key={p.provider} value={p.provider}>
                {p.provider.charAt(0).toUpperCase() + p.provider.slice(1)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl fullWidth sx={{ mt: 2 }}>
          <InputLabel>Embedding Model</InputLabel>
          <Select
            value={model}
            label="Embedding Model"
            onChange={(e) => setModel(e.target.value)}
          >
            {getModelsForProvider(provider).map((m: any) => (
              <MenuItem key={m.name} value={m.name}>
                {m.name} ({m.dimensions} dimensions)
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <TextField
          fullWidth
          label="Batch Size"
          type="number"
          value={batchSize}
          onChange={(e) => setBatchSize(e.target.value)}
          sx={{ mt: 2 }}
          helperText="Number of documents to process in a single batch"
        />

        <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </Button>
          <Button variant="outlined" disabled={saving}>
            Cancel
          </Button>
        </Box>

        <Alert severity="info" sx={{ mt: 3 }}>
          Changes to embedding settings will only affect new documents. Existing embeddings will not be updated automatically.
        </Alert>
      </Paper>
    </Box>
  );
};

export default EmbeddingsSettings;