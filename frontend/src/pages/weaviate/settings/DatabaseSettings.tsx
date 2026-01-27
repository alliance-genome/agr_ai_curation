import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  Alert,
  Divider,
  CircularProgress,
} from '@mui/material';
import { getEnvVar } from '../../../utils/env';

interface HealthData {
  status: string;
  checks: {
    weaviate: string;
  };
  details?: {
    weaviate?: {
      version: string;
      nodes: number;
      collections: number;
    };
  };
}

interface SettingsData {
  database?: {
    collection_name: string;
    schema_version: string;
  };
}

const DatabaseSettings: React.FC = () => {
  const [host, setHost] = useState('weaviate');
  const [port, setPort] = useState('8080');
  const [healthData, setHealthData] = useState<HealthData | null>(null);
  const [settingsData, setSettingsData] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        // Fetch health data
        const healthResponse = await fetch('/api/weaviate/health');
        const health = await healthResponse.json();
        setHealthData(health);

        // Fetch settings data
        const settingsResponse = await fetch('/api/weaviate/settings');
        const settings = await settingsResponse.json();
        setSettingsData(settings);

        // Extract host and port from environment or use defaults
        // These would typically come from environment config
        setHost(getEnvVar(['VITE_WEAVIATE_HOST', 'REACT_APP_WEAVIATE_HOST', 'WEAVIATE_HOST'], 'weaviate') || 'weaviate');
        setPort(getEnvVar(['VITE_WEAVIATE_PORT', 'REACT_APP_WEAVIATE_PORT', 'WEAVIATE_PORT'], '8080') || '8080');
      } catch (err) {
        setError('Failed to load database information');
        console.error('Error fetching data:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    setError(null);

    try {
      const response = await fetch('/api/weaviate/health');
      const data = await response.json();

      if (data.checks?.weaviate === 'healthy') {
        setTestResult('Connection successful!');
      } else {
        setTestResult('Connection failed');
      }
    } catch (err) {
      setTestResult('Connection test failed');
      console.error('Error testing connection:', err);
    } finally {
      setTesting(false);
    }
  };

  const handleSave = () => {
    // Database connection settings are typically environment-based
    // and not changed through the UI
    setError('Database connection settings are managed through environment configuration');
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
        Database Settings
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {testResult && (
        <Alert
          severity={testResult.includes('successful') ? 'success' : 'error'}
          sx={{ mb: 2 }}
          onClose={() => setTestResult(null)}
        >
          {testResult}
        </Alert>
      )}

      <Paper sx={{ p: 3, mt: 3 }}>
        <Typography variant="h6" gutterBottom>
          Weaviate Connection
        </Typography>

        <TextField
          fullWidth
          label="Host"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          sx={{ mt: 2 }}
          helperText="Weaviate server hostname or IP address"
          disabled // Disabled as these are environment-based
        />

        <TextField
          fullWidth
          label="Port"
          value={port}
          onChange={(e) => setPort(e.target.value)}
          sx={{ mt: 2 }}
          helperText="Weaviate server port (default: 8080)"
          disabled // Disabled as these are environment-based
        />

        <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
          <Button
            variant="outlined"
            onClick={handleTestConnection}
            disabled={testing}
          >
            {testing ? 'Testing...' : 'Test Connection'}
          </Button>
        </Box>

        <Divider sx={{ my: 3 }} />

        <Typography variant="h6" gutterBottom>
          Database Information
        </Typography>

        <Box sx={{ mt: 2 }}>
          <Typography variant="body2" color="text.secondary">
            Status:{' '}
            <strong
              style={{
                color: healthData?.checks?.weaviate === 'healthy' ? '#4caf50' : '#f44336'
              }}
            >
              {healthData?.checks?.weaviate === 'healthy' ? 'Connected' : 'Disconnected'}
            </strong>
          </Typography>
          {healthData?.details?.weaviate?.version && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Version: {healthData.details.weaviate.version}
            </Typography>
          )}
          {healthData?.details?.weaviate?.nodes !== undefined && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Nodes: {healthData.details.weaviate.nodes}
            </Typography>
          )}
          {healthData?.details?.weaviate?.collections !== undefined && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Collections: {healthData.details.weaviate.collections}
            </Typography>
          )}
          {settingsData?.database?.collection_name && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Collection Name: {settingsData.database.collection_name}
            </Typography>
          )}
          {settingsData?.database?.schema_version && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Schema Version: {settingsData.database.schema_version}
            </Typography>
          )}
        </Box>

        <Box sx={{ mt: 4, display: 'flex', gap: 2 }}>
          <Button variant="contained" onClick={handleSave}>
            Save Changes
          </Button>
          <Button variant="outlined">
            Cancel
          </Button>
        </Box>
      </Paper>
    </Box>
  );
};

export default DatabaseSettings;
