import React, { useState } from 'react';
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
  Divider,
  Grid,
  Alert,
  Snackbar,
  Card,
  CardContent,
  Slider,
  FormHelperText,
  Tabs,
  Tab,
} from '@mui/material';
import {
  Save,
  Refresh,
  Settings as SettingsIcon,
  Storage,
  Schema,
} from '@mui/icons-material';
import ChunkingStrategySelector from '../../components/weaviate/ChunkingStrategySelector';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`settings-tabpanel-${index}`}
      aria-labelledby={`settings-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ py: 3 }}>{children}</Box>}
    </div>
  );
}

interface EmbeddingConfiguration {
  modelProvider: 'openai' | 'cohere' | 'huggingface';
  modelName: string;
  dimensions: number;
  batchSize: number;
}

interface WeaviateSettings {
  collectionName: string;
  schemaVersion: string;
  replicationFactor: number;
  consistency: 'eventual' | 'quorum' | 'all';
  vectorIndexType: 'hnsw' | 'flat';
}

interface SettingsProps {
  embeddingConfig?: EmbeddingConfiguration;
  weaviateSettings?: WeaviateSettings;
  onSaveEmbedding?: (config: EmbeddingConfiguration) => void;
  onSaveWeaviate?: (settings: WeaviateSettings) => void;
}

const Settings: React.FC<SettingsProps> = ({
  embeddingConfig = {
    modelProvider: 'openai',
    modelName: 'text-embedding-3-small',
    dimensions: 1536,
    batchSize: 50,
  },
  weaviateSettings = {
    collectionName: 'PDFDocuments',
    schemaVersion: '1.0.0',
    replicationFactor: 1,
    consistency: 'eventual',
    vectorIndexType: 'hnsw',
  },
  onSaveEmbedding,
  onSaveWeaviate,
}) => {
  const [tabValue, setTabValue] = useState(0);
  const [embedding, setEmbedding] = useState<EmbeddingConfiguration>(embeddingConfig);
  const [weaviate, setWeaviate] = useState<WeaviateSettings>(weaviateSettings);
  const [showSuccessSnackbar, setShowSuccessSnackbar] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');

  const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
    setTabValue(newValue);
  };

  const handleEmbeddingSave = () => {
    onSaveEmbedding?.(embedding);
    setSuccessMessage('Embedding configuration saved successfully');
    setShowSuccessSnackbar(true);
  };

  const handleWeaviateSave = () => {
    onSaveWeaviate?.(weaviate);
    setSuccessMessage('Weaviate settings saved successfully');
    setShowSuccessSnackbar(true);
  };

  const getModelOptions = (provider: string) => {
    switch (provider) {
      case 'openai':
        return [
          { value: 'text-embedding-3-small', label: 'Text Embedding 3 Small', dimensions: 1536 },
          { value: 'text-embedding-3-large', label: 'Text Embedding 3 Large', dimensions: 3072 },
          { value: 'text-embedding-ada-002', label: 'Ada v2', dimensions: 1536 },
        ];
      case 'cohere':
        return [
          { value: 'embed-english-v3.0', label: 'Embed English v3.0', dimensions: 1024 },
          { value: 'embed-multilingual-v3.0', label: 'Embed Multilingual v3.0', dimensions: 1024 },
        ];
      case 'huggingface':
        return [
          { value: 'sentence-transformers/all-MiniLM-L6-v2', label: 'MiniLM L6 v2', dimensions: 384 },
          { value: 'sentence-transformers/all-mpnet-base-v2', label: 'MPNet Base v2', dimensions: 768 },
        ];
      default:
        return [];
    }
  };

  return (
    <Box sx={{ width: '100%', p: 3 }}>
      <Typography variant="h4" sx={{ mb: 3 }}>
        Weaviate Settings
      </Typography>

      <Paper sx={{ width: '100%' }}>
        <Tabs
          value={tabValue}
          onChange={handleTabChange}
          indicatorColor="primary"
          textColor="primary"
          sx={{ borderBottom: 1, borderColor: 'divider' }}
        >
          <Tab icon={<SettingsIcon />} label="Embeddings" />
          <Tab icon={<Storage />} label="Database" />
          <Tab icon={<Schema />} label="Schema" />
        </Tabs>

        <Box sx={{ p: 3 }}>
          <TabPanel value={tabValue} index={0}>
            <Grid container spacing={3}>
              <Grid item xs={12} md={6}>
                <Card>
                  <CardContent>
                    <Typography variant="h6" gutterBottom>
                      Embedding Model Configuration
                    </Typography>
                    <Divider sx={{ mb: 3 }} />

                    <FormControl fullWidth sx={{ mb: 3 }}>
                      <InputLabel>Model Provider</InputLabel>
                      <Select
                        value={embedding.modelProvider}
                        label="Model Provider"
                        onChange={(e) =>
                          setEmbedding({
                            ...embedding,
                            modelProvider: e.target.value as EmbeddingConfiguration['modelProvider'],
                          })
                        }
                      >
                        <MenuItem value="openai">OpenAI</MenuItem>
                        <MenuItem value="cohere">Cohere</MenuItem>
                        <MenuItem value="huggingface">HuggingFace</MenuItem>
                      </Select>
                    </FormControl>

                    <FormControl fullWidth sx={{ mb: 3 }}>
                      <InputLabel>Model Name</InputLabel>
                      <Select
                        value={embedding.modelName}
                        label="Model Name"
                        onChange={(e) => {
                          const model = getModelOptions(embedding.modelProvider).find(
                            (m) => m.value === e.target.value
                          );
                          setEmbedding({
                            ...embedding,
                            modelName: e.target.value,
                            dimensions: model?.dimensions || embedding.dimensions,
                          });
                        }}
                      >
                        {getModelOptions(embedding.modelProvider).map((model) => (
                          <MenuItem key={model.value} value={model.value}>
                            {model.label}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>

                    <TextField
                      fullWidth
                      label="Vector Dimensions"
                      type="number"
                      value={embedding.dimensions}
                      disabled
                      sx={{ mb: 3 }}
                      helperText="Automatically set based on model selection"
                    />

                    <Box sx={{ mb: 3 }}>
                      <Typography gutterBottom>
                        Batch Size: {embedding.batchSize}
                      </Typography>
                      <Slider
                        value={embedding.batchSize}
                        onChange={(_e, value) =>
                          setEmbedding({ ...embedding, batchSize: value as number })
                        }
                        min={1}
                        max={100}
                        marks={[
                          { value: 1, label: '1' },
                          { value: 50, label: '50' },
                          { value: 100, label: '100' },
                        ]}
                      />
                      <FormHelperText>
                        Number of documents to process in parallel
                      </FormHelperText>
                    </Box>

                    <Box sx={{ display: 'flex', gap: 2 }}>
                      <Button
                        variant="contained"
                        startIcon={<Save />}
                        onClick={handleEmbeddingSave}
                      >
                        Save Configuration
                      </Button>
                      <Button
                        variant="outlined"
                        startIcon={<Refresh />}
                        onClick={() => setEmbedding(embeddingConfig)}
                      >
                        Reset
                      </Button>
                    </Box>
                  </CardContent>
                </Card>
              </Grid>

              <Grid item xs={12} md={6}>
                <Card>
                  <CardContent>
                    <Typography variant="h6" gutterBottom>
                      Chunking Strategy
                    </Typography>
                    <Divider sx={{ mb: 3 }} />
                    <ChunkingStrategySelector />
                  </CardContent>
                </Card>
              </Grid>
            </Grid>
          </TabPanel>

          <TabPanel value={tabValue} index={1}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Database Configuration
                </Typography>
                <Divider sx={{ mb: 3 }} />

                <Grid container spacing={3}>
                  <Grid item xs={12} md={6}>
                    <TextField
                      fullWidth
                      label="Collection Name"
                      value={weaviate.collectionName}
                      onChange={(e) =>
                        setWeaviate({ ...weaviate, collectionName: e.target.value })
                      }
                      sx={{ mb: 3 }}
                      helperText="Name of the Weaviate collection"
                    />

                    <TextField
                      fullWidth
                      label="Schema Version"
                      value={weaviate.schemaVersion}
                      onChange={(e) =>
                        setWeaviate({ ...weaviate, schemaVersion: e.target.value })
                      }
                      sx={{ mb: 3 }}
                      helperText="Current schema version (semantic versioning)"
                    />

                    <TextField
                      fullWidth
                      label="Replication Factor"
                      type="number"
                      value={weaviate.replicationFactor}
                      onChange={(e) =>
                        setWeaviate({
                          ...weaviate,
                          replicationFactor: parseInt(e.target.value),
                        })
                      }
                      sx={{ mb: 3 }}
                      inputProps={{ min: 1, max: 10 }}
                      helperText="Number of data replicas"
                    />
                  </Grid>

                  <Grid item xs={12} md={6}>
                    <FormControl fullWidth sx={{ mb: 3 }}>
                      <InputLabel>Consistency Level</InputLabel>
                      <Select
                        value={weaviate.consistency}
                        label="Consistency Level"
                        onChange={(e) =>
                          setWeaviate({
                            ...weaviate,
                            consistency: e.target.value as WeaviateSettings['consistency'],
                          })
                        }
                      >
                        <MenuItem value="eventual">Eventual</MenuItem>
                        <MenuItem value="quorum">Quorum</MenuItem>
                        <MenuItem value="all">All</MenuItem>
                      </Select>
                      <FormHelperText>
                        Read/write consistency requirements
                      </FormHelperText>
                    </FormControl>

                    <FormControl fullWidth sx={{ mb: 3 }}>
                      <InputLabel>Vector Index Type</InputLabel>
                      <Select
                        value={weaviate.vectorIndexType}
                        label="Vector Index Type"
                        onChange={(e) =>
                          setWeaviate({
                            ...weaviate,
                            vectorIndexType: e.target.value as WeaviateSettings['vectorIndexType'],
                          })
                        }
                      >
                        <MenuItem value="hnsw">HNSW</MenuItem>
                        <MenuItem value="flat">Flat</MenuItem>
                      </Select>
                      <FormHelperText>
                        Index type for vector similarity search
                      </FormHelperText>
                    </FormControl>
                  </Grid>
                </Grid>

                <Box sx={{ display: 'flex', gap: 2, mt: 3 }}>
                  <Button
                    variant="contained"
                    startIcon={<Save />}
                    onClick={handleWeaviateSave}
                  >
                    Save Settings
                  </Button>
                  <Button
                    variant="outlined"
                    startIcon={<Refresh />}
                    onClick={() => setWeaviate(weaviateSettings)}
                  >
                    Reset
                  </Button>
                </Box>
              </CardContent>
            </Card>
          </TabPanel>

          <TabPanel value={tabValue} index={2}>
            <Alert severity="info" sx={{ mb: 3 }}>
              Schema management features are currently in development. You can view and modify
              collection schemas directly through the Weaviate API.
            </Alert>

            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Current Schema Information
                </Typography>
                <Divider sx={{ mb: 3 }} />

                <Grid container spacing={2}>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Collection Name
                    </Typography>
                    <Typography variant="body1" sx={{ mb: 2 }}>
                      {weaviate.collectionName}
                    </Typography>
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Schema Version
                    </Typography>
                    <Typography variant="body1" sx={{ mb: 2 }}>
                      {weaviate.schemaVersion}
                    </Typography>
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Vector Index Type
                    </Typography>
                    <Typography variant="body1" sx={{ mb: 2 }}>
                      {weaviate.vectorIndexType.toUpperCase()}
                    </Typography>
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Vector Dimensions
                    </Typography>
                    <Typography variant="body1" sx={{ mb: 2 }}>
                      {embedding.dimensions}
                    </Typography>
                  </Grid>
                </Grid>
              </CardContent>
            </Card>
          </TabPanel>
        </Box>
      </Paper>

      <Snackbar
        open={showSuccessSnackbar}
        autoHideDuration={3000}
        onClose={() => setShowSuccessSnackbar(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setShowSuccessSnackbar(false)}
          severity="success"
          variant="filled"
        >
          {successMessage}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default Settings;