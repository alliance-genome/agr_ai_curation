import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Container,
  Paper,
  Typography,
  TextField,
  Button,
  Box,
  Divider,
  Alert,
  Snackbar,
  IconButton,
} from '@mui/material';
import { ArrowBack, Save } from '@mui/icons-material';
import axios from 'axios';

interface Settings {
  openai_api_key: string;
  anthropic_api_key: string;
  default_model: string;
  max_tokens: number;
  temperature: number;
  database_url: string;
  debug_mode: boolean;
}

function AdminPage() {
  const navigate = useNavigate();
  const [settings, setSettings] = useState<Settings>({
    openai_api_key: '',
    anthropic_api_key: '',
    default_model: 'gpt-4',
    max_tokens: 2048,
    temperature: 0.7,
    database_url: '',
    debug_mode: false,
  });
  const [loading, setLoading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      const response = await axios.get('/api/settings');
      setSettings(response.data);
    } catch (error) {
      console.error('Failed to fetch settings:', error);
      setSnackbar({ open: true, message: 'Failed to load settings', severity: 'error' });
    }
  };

  const handleChange = (field: keyof Settings) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
    setSettings({
      ...settings,
      [field]: value,
    });
    setDirty(true);
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      await axios.put('/api/settings', settings);
      setSnackbar({ open: true, message: 'Settings saved successfully', severity: 'success' });
      setDirty(false);
    } catch (error) {
      console.error('Failed to save settings:', error);
      setSnackbar({ open: true, message: 'Failed to save settings', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleSnackbarClose = () => {
    setSnackbar({ ...snackbar, open: false });
  };

  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Paper sx={{ p: 4 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 3 }}>
          <IconButton onClick={() => navigate('/')} sx={{ mr: 2 }}>
            <ArrowBack />
          </IconButton>
          <Typography variant="h4" component="h1" sx={{ flexGrow: 1 }}>
            Admin Settings
          </Typography>
        </Box>

        {dirty && (
          <Alert severity="warning" sx={{ mb: 3 }}>
            You have unsaved changes.
          </Alert>
        )}

        <Box component="form" noValidate autoComplete="off">
          <Typography variant="h6" sx={{ mb: 2 }}>
            API Configuration
          </Typography>
          
          <Box sx={{ display: 'grid', gap: 2, mb: 3 }}>
            <TextField
              fullWidth
              label="OpenAI API Key"
              type="password"
              value={settings.openai_api_key}
              onChange={handleChange('openai_api_key')}
              helperText="Your OpenAI API key for GPT models"
            />
            
            <TextField
              fullWidth
              label="Anthropic API Key"
              type="password"
              value={settings.anthropic_api_key}
              onChange={handleChange('anthropic_api_key')}
              helperText="Your Anthropic API key for Claude models"
            />
          </Box>

          <Divider sx={{ my: 3 }} />

          <Typography variant="h6" sx={{ mb: 2 }}>
            Model Settings
          </Typography>
          
          <Box sx={{ display: 'grid', gap: 2, mb: 3, gridTemplateColumns: '1fr 1fr' }}>
            <TextField
              fullWidth
              label="Default Model"
              value={settings.default_model}
              onChange={handleChange('default_model')}
              helperText="Default AI model to use"
            />
            
            <TextField
              fullWidth
              label="Max Tokens"
              type="number"
              value={settings.max_tokens}
              onChange={handleChange('max_tokens')}
              helperText="Maximum tokens in responses"
            />
            
            <TextField
              fullWidth
              label="Temperature"
              type="number"
              value={settings.temperature}
              onChange={handleChange('temperature')}
              inputProps={{ step: 0.1, min: 0, max: 2 }}
              helperText="Model temperature (0-2)"
            />
          </Box>

          <Divider sx={{ my: 3 }} />

          <Typography variant="h6" sx={{ mb: 2 }}>
            Database Configuration
          </Typography>
          
          <TextField
            fullWidth
            label="Database URL"
            value={settings.database_url}
            onChange={handleChange('database_url')}
            helperText="PostgreSQL connection string"
            sx={{ mb: 3 }}
          />

          <Button
            variant="contained"
            size="large"
            startIcon={<Save />}
            onClick={handleSave}
            disabled={loading || !dirty}
          >
            Save Settings
          </Button>
        </Box>
      </Paper>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={handleSnackbarClose}
      >
        <Alert onClose={handleSnackbarClose} severity={snackbar.severity}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}

export default AdminPage;