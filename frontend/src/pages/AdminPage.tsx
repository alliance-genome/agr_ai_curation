import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  AppBar,
  Toolbar,
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  Divider,
  Alert,
  Snackbar,
  Grid,
  FormControl,
  InputLabel,
  Select,
  SelectChangeEvent,
  MenuItem,
  Switch,
  FormControlLabel,
  Stack,
  IconButton,
} from "@mui/material";
import {
  Home as HomeIcon,
  AdminPanelSettings as AdminIcon,
  Settings as SettingsIcon,
  Brightness4,
  Brightness7,
  Save,
} from "@mui/icons-material";
import axios from "axios";
import { debug } from "../utils/debug";
import { useTheme } from "@mui/material/styles";

const SECRET_MASK = "************"; // pragma: allowlist secret

type SnackbarSeverity = "success" | "error";

type SecretField = "openai_api_key" | "anthropic_api_key"; // pragma: allowlist secret

type NumericField =
  | "max_tokens"
  | "temperature"
  | "embedding_dimensions"
  | "embedding_max_batch_size"
  | "embedding_default_batch_size";

type TextFieldKey =
  | "default_model"
  | "database_url"
  | "embedding_model_name"
  | "embedding_model_version";

interface SettingsState {
  openai_api_key: string;
  openai_api_key_masked: boolean;
  anthropic_api_key: string;
  anthropic_api_key_masked: boolean;
  default_model: string;
  max_tokens: string;
  temperature: string;
  database_url: string;
  debug_mode: boolean;
  embedding_model_name: string;
  embedding_model_version: string;
  embedding_dimensions: string;
  embedding_max_batch_size: string;
  embedding_default_batch_size: string;
  pdf_extraction_strategy: string;
}

interface AdminPageProps {
  toggleColorMode: () => void;
}

const initialState: SettingsState = {
  openai_api_key: "",
  openai_api_key_masked: false,
  anthropic_api_key: "",
  anthropic_api_key_masked: false,
  default_model: "gpt-4o",
  max_tokens: "2048",
  temperature: "0.7",
  database_url: "",
  debug_mode: false,
  embedding_model_name: "text-embedding-3-small",
  embedding_model_version: "1.0",
  embedding_dimensions: "1536",
  embedding_max_batch_size: "128",
  embedding_default_batch_size: "64",
  pdf_extraction_strategy: "fast",
};

function AdminPage({ toggleColorMode }: AdminPageProps) {
  const navigate = useNavigate();
  const theme = useTheme();
  const [settings, setSettings] = useState<SettingsState>(initialState);
  const [loading, setLoading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "success" as SnackbarSeverity,
  });

  const parseString = (value: unknown, fallback: string) =>
    value === undefined || value === null || value === ""
      ? fallback
      : String(value);

  const fetchSettings = async () => {
    try {
      const response = await axios.get("/api/settings/");
      const data = response.data ?? {};
      setSettings({
        openai_api_key: data.openai_api_key_masked
          ? SECRET_MASK
          : parseString(data.openai_api_key, ""),
        openai_api_key_masked: Boolean(data.openai_api_key_masked),
        anthropic_api_key: data.anthropic_api_key_masked
          ? SECRET_MASK
          : parseString(data.anthropic_api_key, ""),
        anthropic_api_key_masked: Boolean(data.anthropic_api_key_masked),
        default_model: parseString(
          data.default_model,
          initialState.default_model,
        ),
        max_tokens: parseString(data.max_tokens, initialState.max_tokens),
        temperature: parseString(data.temperature, initialState.temperature),
        database_url: parseString(data.database_url, ""),
        debug_mode: Boolean(data.debug_mode),
        embedding_model_name: parseString(
          data.embedding_model_name,
          initialState.embedding_model_name,
        ),
        embedding_model_version: parseString(
          data.embedding_model_version,
          initialState.embedding_model_version,
        ),
        embedding_dimensions: parseString(
          data.embedding_dimensions,
          initialState.embedding_dimensions,
        ),
        embedding_max_batch_size: parseString(
          data.embedding_max_batch_size,
          initialState.embedding_max_batch_size,
        ),
        embedding_default_batch_size: parseString(
          data.embedding_default_batch_size,
          initialState.embedding_default_batch_size,
        ),
        pdf_extraction_strategy: parseString(
          data.pdf_extraction_strategy,
          initialState.pdf_extraction_strategy,
        ),
      });
      setDirty(false);
    } catch (error) {
      console.error("Failed to fetch settings:", error);
      setSnackbar({
        open: true,
        message: "Failed to load settings",
        severity: "error",
      });
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleSecretChange =
    (field: SecretField) => (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setSettings((prev) => {
        if (field === "openai_api_key") {
          return {
            ...prev,
            openai_api_key: value,
            openai_api_key_masked:
              value === SECRET_MASK ? prev.openai_api_key_masked : false,
          };
        }
        return {
          ...prev,
          anthropic_api_key: value,
          anthropic_api_key_masked:
            value === SECRET_MASK ? prev.anthropic_api_key_masked : false,
        };
      });
      setDirty(true);
    };

  const handleTextChange =
    (field: TextFieldKey) => (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setSettings((prev) => ({
        ...prev,
        [field]: value,
      }));
      setDirty(true);
    };

  const handleNumberChange =
    (field: NumericField) => (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setSettings((prev) => ({
        ...prev,
        [field]: value,
      }));
      setDirty(true);
    };

  const handleModelSelect = (value: string) => {
    setSettings((prev) => ({
      ...prev,
      default_model: value,
    }));
    setDirty(true);
  };

  const handleExtractionStrategyChange = (event: SelectChangeEvent<string>) => {
    const value = event.target.value as string;
    setSettings((prev) => ({
      ...prev,
      pdf_extraction_strategy: value,
    }));
    setDirty(true);
  };

  const handleDebugToggle = (event: React.ChangeEvent<HTMLInputElement>) => {
    const enabled = event.target.checked;
    setSettings((prev) => ({
      ...prev,
      debug_mode: enabled,
    }));
    setDirty(true);
    debug.setEnabled(enabled);
    debug.settings(`Debug mode ${enabled ? "enabled" : "disabled"}`);
  };

  const handleSave = async () => {
    const numericValidations: Array<{
      key: NumericField;
      value: string;
      label: string;
      parser: (val: string) => number;
    }> = [
      {
        key: "max_tokens",
        value: settings.max_tokens,
        label: "Max Tokens",
        parser: (val) => parseInt(val, 10),
      },
      {
        key: "temperature",
        value: settings.temperature,
        label: "Temperature",
        parser: (val) => parseFloat(val),
      },
      {
        key: "embedding_dimensions",
        value: settings.embedding_dimensions,
        label: "Embedding Dimensions",
        parser: (val) => parseInt(val, 10),
      },
      {
        key: "embedding_max_batch_size",
        value: settings.embedding_max_batch_size,
        label: "Embedding Max Batch Size",
        parser: (val) => parseInt(val, 10),
      },
      {
        key: "embedding_default_batch_size",
        value: settings.embedding_default_batch_size,
        label: "Embedding Default Batch Size",
        parser: (val) => parseInt(val, 10),
      },
    ];

    const payload: Record<string, unknown> = {};

    for (const { key, value, label, parser } of numericValidations) {
      if (value === "") {
        setSnackbar({
          open: true,
          message: `${label} cannot be empty`,
          severity: "error",
        });
        return;
      }

      const parsed = parser(value);
      if (Number.isNaN(parsed)) {
        setSnackbar({
          open: true,
          message: `${label} must be a number`,
          severity: "error",
        });
        return;
      }
      payload[key] = parsed;
    }

    payload.default_model = settings.default_model;
    payload.database_url = settings.database_url;
    payload.debug_mode = settings.debug_mode;
    payload.embedding_model_name = settings.embedding_model_name;
    payload.embedding_model_version = settings.embedding_model_version;
    payload.pdf_extraction_strategy = settings.pdf_extraction_strategy;

    const secretFields: Array<{
      key: SecretField;
      masked: boolean;
      value: string;
    }> = [
      {
        key: "openai_api_key",
        masked: settings.openai_api_key_masked,
        value: settings.openai_api_key,
      },
      {
        key: "anthropic_api_key",
        masked: settings.anthropic_api_key_masked,
        value: settings.anthropic_api_key,
      },
    ];

    for (const secret of secretFields) {
      if (secret.masked && secret.value === SECRET_MASK) {
        continue;
      }
      payload[secret.key] = secret.value;
    }

    setLoading(true);
    try {
      await axios.put("/api/settings/", payload);
      setSnackbar({
        open: true,
        message: "Settings saved successfully",
        severity: "success",
      });
      setDirty(false);
      await fetchSettings();
    } catch (error) {
      console.error("Failed to save settings:", error);
      setSnackbar({
        open: true,
        message:
          axios.isAxiosError(error) && error.response?.data?.detail
            ? error.response.data.detail
            : "Failed to save settings",
        severity: "error",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleSnackbarClose = () => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <AppBar position="static" sx={{ zIndex: (t) => t.zIndex.drawer + 1 }}>
        <Toolbar>
          <Typography
            variant="h6"
            component="div"
            sx={{ flexGrow: 1, cursor: "pointer" }}
            onClick={() => navigate("/")}
          >
            Alliance AI-Assisted Curation Interface
          </Typography>

          <IconButton onClick={toggleColorMode} color="inherit" sx={{ mr: 1 }}>
            {theme.palette.mode === "dark" ? <Brightness7 /> : <Brightness4 />}
          </IconButton>

          <Button
            color="inherit"
            startIcon={<HomeIcon />}
            onClick={() => navigate("/")}
            sx={{ mr: 1 }}
          >
            Home
          </Button>
          <Button
            color="inherit"
            startIcon={<AdminIcon />}
            onClick={() => navigate("/admin")}
            sx={{ mr: 1 }}
          >
            Admin
          </Button>
          <Button
            color="inherit"
            startIcon={<SettingsIcon />}
            onClick={() => navigate("/settings")}
          >
            Settings
          </Button>
        </Toolbar>
      </AppBar>

      <Box
        component="main"
        sx={{ width: "100%", flex: 1, bgcolor: "background.default", py: 4 }}
      >
        <Box sx={{ maxWidth: "1200px", mx: "auto", px: { xs: 2, md: 4 } }}>
          <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
            <Typography variant="h4" component="h1" sx={{ flexGrow: 1 }}>
              Admin Settings
            </Typography>
          </Stack>

          {dirty && (
            <Alert severity="warning" sx={{ mb: 3 }}>
              You have unsaved changes.
            </Alert>
          )}

          <Paper sx={{ p: { xs: 3, md: 4 }, mb: 4 }}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={2}
              alignItems={{ xs: "flex-start", md: "center" }}
              justifyContent="space-between"
              sx={{ mb: 3 }}
            >
              <Typography variant="h6" component="h2">
                Configuration
              </Typography>
              <Button
                variant="contained"
                startIcon={<Save />}
                onClick={handleSave}
                disabled={loading}
              >
                Save Changes
              </Button>
            </Stack>

            <Typography variant="subtitle1" sx={{ mb: 2 }}>
              API Keys
            </Typography>
            <Grid container spacing={2} sx={{ mb: 3 }}>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="OpenAI API Key"
                  type="password"
                  value={settings.openai_api_key}
                  onChange={handleSecretChange("openai_api_key")}
                  helperText={
                    settings.openai_api_key_masked
                      ? "Loaded from environment. Leave masked to keep current value."
                      : "Your OpenAI API key for GPT models"
                  }
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Anthropic API Key"
                  type="password"
                  value={settings.anthropic_api_key}
                  onChange={handleSecretChange("anthropic_api_key")}
                  helperText={
                    settings.anthropic_api_key_masked
                      ? "Loaded from environment. Leave masked to keep current value."
                      : "Your Anthropic API key for Claude models"
                  }
                />
              </Grid>
            </Grid>

            <Divider sx={{ my: 3 }} />

            <Typography variant="subtitle1" sx={{ mb: 2 }}>
              Language Model Defaults
            </Typography>
            <Grid container spacing={2} sx={{ mb: 3 }}>
              <Grid item xs={12} md={6}>
                <FormControl fullWidth>
                  <InputLabel>Default Model</InputLabel>
                  <Select
                    value={settings.default_model}
                    label="Default Model"
                    onChange={(event) => handleModelSelect(event.target.value)}
                  >
                    <MenuItem value="gpt-4o">GPT-4o</MenuItem>
                    <MenuItem value="gpt-4o-mini">GPT-4o Mini</MenuItem>
                    <MenuItem value="gpt-4-turbo">GPT-4 Turbo</MenuItem>
                    <MenuItem value="gpt-4">GPT-4</MenuItem>
                    <MenuItem value="gpt-3.5-turbo">GPT-3.5 Turbo</MenuItem>
                    <MenuItem value="claude-3-opus">Claude 3 Opus</MenuItem>
                    <MenuItem value="claude-3-sonnet">Claude 3 Sonnet</MenuItem>
                    <MenuItem value="claude-3-haiku">Claude 3 Haiku</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} md={3}>
                <TextField
                  fullWidth
                  label="Max Tokens"
                  type="number"
                  value={settings.max_tokens}
                  onChange={handleNumberChange("max_tokens")}
                  helperText="Maximum tokens per response"
                />
              </Grid>
              <Grid item xs={12} md={3}>
                <TextField
                  fullWidth
                  label="Temperature"
                  type="number"
                  value={settings.temperature}
                  onChange={handleNumberChange("temperature")}
                  inputProps={{ step: 0.1, min: 0, max: 2 }}
                  helperText="Response randomness (0-2)"
                />
              </Grid>
            </Grid>

            <TextField
              fullWidth
              label="Database URL"
              value={settings.database_url}
              onChange={handleTextChange("database_url")}
              helperText="Database connection string"
              sx={{ mb: 3 }}
            />

            <FormControlLabel
              control={
                <Switch
                  checked={settings.debug_mode}
                  onChange={handleDebugToggle}
                  color="primary"
                />
              }
              label="Enable Debug Mode"
            />

            <Divider sx={{ my: 3 }} />

            <Typography variant="subtitle1" sx={{ mb: 2 }}>
              PDF Processing
            </Typography>
            <Grid container spacing={2} sx={{ mb: 1 }}>
              <Grid item xs={12} md={6}>
                <FormControl fullWidth>
                  <InputLabel>Extraction Strategy</InputLabel>
                  <Select
                    value={settings.pdf_extraction_strategy}
                    label="Extraction Strategy"
                    onChange={handleExtractionStrategyChange}
                  >
                    <MenuItem value="fast">Fast (default)</MenuItem>
                    <MenuItem value="hi_res">High Resolution</MenuItem>
                    <MenuItem value="ocr_only">OCR Only</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
            </Grid>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
              Choose the Unstructured extraction mode used when new PDFs are
              ingested. High resolution offers the best layout fidelity at the
              cost of longer processing times.
            </Typography>

            <Divider sx={{ my: 3 }} />

            <Typography variant="subtitle1" sx={{ mb: 2 }}>
              Embedding Settings
            </Typography>
            <Grid container spacing={2} sx={{ mb: 3 }}>
              <Grid item xs={12} md={4}>
                <TextField
                  fullWidth
                  label="Embedding Model Name"
                  value={settings.embedding_model_name}
                  onChange={handleTextChange("embedding_model_name")}
                  helperText="Embedding provider model identifier"
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  fullWidth
                  label="Embedding Model Version"
                  value={settings.embedding_model_version}
                  onChange={handleTextChange("embedding_model_version")}
                  helperText="Version tag for embedding model"
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  fullWidth
                  label="Embedding Dimensions"
                  type="number"
                  value={settings.embedding_dimensions}
                  onChange={handleNumberChange("embedding_dimensions")}
                  helperText="Vector dimension produced by the model"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Embedding Max Batch Size"
                  type="number"
                  value={settings.embedding_max_batch_size}
                  onChange={handleNumberChange("embedding_max_batch_size")}
                  helperText="Upper bound for batching chunks"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Embedding Default Batch Size"
                  type="number"
                  value={settings.embedding_default_batch_size}
                  onChange={handleNumberChange("embedding_default_batch_size")}
                  helperText="Default batch size when unspecified"
                />
              </Grid>
            </Grid>

            <Divider sx={{ my: 3 }} />

            <Typography variant="subtitle1" sx={{ mb: 2 }}>
              Developer Tools
            </Typography>
            <Stack spacing={2}>
              <Typography variant="body2" color="text.secondary">
                Toggle debug mode to surface detailed logs in the browser
                console or reset local UI state using the quick actions below.
              </Typography>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <Button
                  variant="outlined"
                  onClick={() => {
                    localStorage.removeItem("annotationsViewed");
                    setSnackbar({
                      open: true,
                      message:
                        "Annotations badge reset. Refresh the page to see it.",
                      severity: "success",
                    });
                  }}
                >
                  Reset Annotations Badge
                </Button>
                <Button
                  variant="outlined"
                  onClick={() => {
                    localStorage.clear();
                    setSnackbar({
                      open: true,
                      message:
                        "All local storage cleared. Refresh the page to see changes.",
                      severity: "success",
                    });
                  }}
                >
                  Clear Local Storage
                </Button>
              </Stack>
            </Stack>
          </Paper>

          <Snackbar
            open={snackbar.open}
            autoHideDuration={6000}
            onClose={handleSnackbarClose}
            anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
          >
            <Alert
              severity={snackbar.severity}
              onClose={handleSnackbarClose}
              sx={{ width: "100%" }}
            >
              {snackbar.message}
            </Alert>
          </Snackbar>
        </Box>
      </Box>
    </Box>
  );
}

export default AdminPage;
