import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
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
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Switch,
  FormControlLabel,
} from "@mui/material";
import { ArrowBack, Save } from "@mui/icons-material";
import axios from "axios";
import { debug } from "../utils/debug";

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
    openai_api_key: "",
    anthropic_api_key: "",
    default_model: "gpt-4o",
    max_tokens: 2048,
    temperature: 0.7,
    database_url: "",
    debug_mode: false,
  });
  const [loading, setLoading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "success" as "success" | "error",
  });

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      const response = await axios.get("/api/settings");
      setSettings(response.data);
    } catch (error) {
      console.error("Failed to fetch settings:", error);
      setSnackbar({
        open: true,
        message: "Failed to load settings",
        severity: "error",
      });
    }
  };

  const handleChange =
    (field: keyof Settings) => (event: React.ChangeEvent<HTMLInputElement>) => {
      const value =
        event.target.type === "checkbox"
          ? event.target.checked
          : event.target.value;
      setSettings({
        ...settings,
        [field]: value,
      });
      setDirty(true);

      // Update debug mode immediately if that's what changed
      if (field === "debug_mode") {
        debug.setEnabled(value as boolean);
        debug.settings(`Debug mode ${value ? "enabled" : "disabled"}`);
      }
    };

  const handleSave = async () => {
    setLoading(true);
    try {
      await axios.put("/api/settings", settings);
      setSnackbar({
        open: true,
        message: "Settings saved successfully",
        severity: "success",
      });
      setDirty(false);
    } catch (error) {
      console.error("Failed to save settings:", error);
      setSnackbar({
        open: true,
        message: "Failed to save settings",
        severity: "error",
      });
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
        <Box sx={{ display: "flex", alignItems: "center", mb: 3 }}>
          <IconButton onClick={() => navigate("/")} sx={{ mr: 2 }}>
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

          <Box sx={{ display: "grid", gap: 2, mb: 3 }}>
            <TextField
              fullWidth
              label="OpenAI API Key"
              type="password"
              value={settings.openai_api_key}
              onChange={handleChange("openai_api_key")}
              helperText="Your OpenAI API key for GPT models"
            />

            <TextField
              fullWidth
              label="Anthropic API Key"
              type="password"
              value={settings.anthropic_api_key}
              onChange={handleChange("anthropic_api_key")}
              helperText="Your Anthropic API key for Claude models"
            />
          </Box>

          <Divider sx={{ my: 3 }} />

          <Typography variant="h6" sx={{ mb: 2 }}>
            Model Settings
          </Typography>

          <Box
            sx={{
              display: "grid",
              gap: 2,
              mb: 3,
              gridTemplateColumns: "1fr 1fr",
            }}
          >
            <FormControl fullWidth>
              <InputLabel>Default Model</InputLabel>
              <Select
                value={settings.default_model}
                label="Default Model"
                onChange={(e) => {
                  setSettings({
                    ...settings,
                    default_model: e.target.value,
                  });
                  setDirty(true);
                }}
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

            <TextField
              fullWidth
              label="Max Tokens"
              type="number"
              value={settings.max_tokens}
              onChange={handleChange("max_tokens")}
              helperText="Maximum tokens in responses"
            />

            <TextField
              fullWidth
              label="Temperature"
              type="number"
              value={settings.temperature}
              onChange={handleChange("temperature")}
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
            onChange={handleChange("database_url")}
            helperText="PostgreSQL connection string"
            sx={{ mb: 3 }}
          />

          <Divider sx={{ my: 3 }} />

          <Typography variant="h6" sx={{ mb: 2 }}>
            Developer Options
          </Typography>

          <Box sx={{ mb: 3 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={settings.debug_mode}
                  onChange={(e) => {
                    const newValue = e.target.checked;
                    setSettings({ ...settings, debug_mode: newValue });
                    setDirty(true);
                    debug.setEnabled(newValue);
                    debug.settings(
                      `Debug mode ${newValue ? "enabled" : "disabled"}`,
                    );
                  }}
                  color="primary"
                />
              }
              label="Debug Mode"
            />
            <Typography variant="body2" color="text.secondary" sx={{ ml: 7 }}>
              {settings.debug_mode
                ? "Console debugging is active. Check browser console for detailed logs."
                : "Enable to see detailed debugging information in browser console."}
            </Typography>
          </Box>

          <Divider sx={{ my: 2 }} />

          <Typography variant="body1" sx={{ mb: 2 }}>
            Reset Options
          </Typography>

          <Box sx={{ display: "flex", gap: 2, mb: 3 }}>
            <Button
              variant="outlined"
              size="small"
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
              size="small"
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
              Clear All Local Storage
            </Button>
          </Box>

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
