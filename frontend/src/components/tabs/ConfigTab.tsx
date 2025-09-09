import { useState } from "react";
import {
  Box,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Typography,
  Paper,
  Button,
  SelectChangeEvent,
} from "@mui/material";
import { Save } from "@mui/icons-material";

function ConfigTab() {
  const [config, setConfig] = useState({
    model: "gpt-4",
    highlightColor: "#FFFF00",
    apiEndpoint: "http://localhost:8002",
    maxTokens: "2048",
  });

  const handleSelectChange =
    (field: string) => (event: SelectChangeEvent<string>) => {
      setConfig({
        ...config,
        [field]: event.target.value as string,
      });
    };

  const handleInputChange =
    (field: string) => (event: React.ChangeEvent<HTMLInputElement>) => {
      setConfig({
        ...config,
        [field]: event.target.value,
      });
    };

  const handleSave = () => {
    // Save configuration to backend
    console.log("Saving configuration:", config);
  };

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Configure AI model and interface settings.
      </Typography>

      <Paper sx={{ p: 2 }}>
        <Box sx={{ display: "grid", gap: 2 }}>
          <FormControl fullWidth size="small">
            <InputLabel>AI Model</InputLabel>
            <Select
              value={config.model}
              label="AI Model"
              onChange={handleSelectChange("model")}
            >
              <MenuItem value="gpt-4">GPT-4</MenuItem>
              <MenuItem value="gpt-3.5-turbo">GPT-3.5 Turbo</MenuItem>
              <MenuItem value="claude-3-opus">Claude 3 Opus</MenuItem>
              <MenuItem value="claude-3-sonnet">Claude 3 Sonnet</MenuItem>
            </Select>
          </FormControl>

          <TextField
            fullWidth
            label="Max Tokens"
            value={config.maxTokens}
            onChange={handleInputChange("maxTokens")}
            type="number"
            size="small"
            helperText="Maximum tokens for AI responses"
          />

          <TextField
            fullWidth
            label="Highlight Color"
            value={config.highlightColor}
            onChange={handleInputChange("highlightColor")}
            type="color"
            size="small"
            helperText="Color for highlighting entities in PDF"
            InputLabelProps={{
              shrink: true,
            }}
          />

          <TextField
            fullWidth
            label="API Endpoint"
            value={config.apiEndpoint}
            onChange={handleInputChange("apiEndpoint")}
            size="small"
            helperText="Backend API URL"
          />
        </Box>

        <Button
          variant="contained"
          startIcon={<Save />}
          onClick={handleSave}
          sx={{ mt: 3 }}
        >
          Save Configuration
        </Button>
      </Paper>
    </Box>
  );
}

export default ConfigTab;
