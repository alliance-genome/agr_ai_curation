import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Container,
  Paper,
  Typography,
  Box,
  IconButton,
  Button,
  Slider,
  FormControl,
  FormLabel,
  Divider,
  Grid,
  Card,
  CardContent,
  Snackbar,
  Alert,
} from "@mui/material";
import {
  ArrowBack,
  RestoreOutlined,
  Save,
  Add,
  Delete,
} from "@mui/icons-material";

const SETTINGS_KEY = "alliance-user-settings";
const DEFAULT_HIGHLIGHT_OPACITY = 0.4;
const DEFAULT_HIGHLIGHT_COLORS = [
  "#ffd54f", // Amber
  "#80deea", // Cyan
  "#c5e1a5", // Light Green
  "#f48fb1", // Pink
  "#ce93d8", // Purple
  "#90caf9", // Blue
  "#ffcc80", // Orange
  "#bcaaa4", // Brown
];

interface UserSettings {
  highlightOpacity: number;
  highlightColors: string[];
}

function SettingsPage() {
  console.log("🔧 SettingsPage: Building step by step - Component mounting");

  const navigate = useNavigate();
  const [settings, setSettings] = useState<UserSettings>({
    highlightOpacity: DEFAULT_HIGHLIGHT_OPACITY,
    highlightColors: [...DEFAULT_HIGHLIGHT_COLORS],
  });
  const [unsavedChanges, setUnsavedChanges] = useState(false);
  const [showSaveSuccess, setShowSaveSuccess] = useState(false);

  console.log("🔧 SettingsPage: State initialized", {
    settings,
    unsavedChanges,
  });

  // Load settings from localStorage
  useEffect(() => {
    console.log("🔧 SettingsPage: Loading from localStorage");
    const savedSettings = localStorage.getItem(SETTINGS_KEY);
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings);
        setSettings({
          highlightOpacity:
            parsed.highlightOpacity || DEFAULT_HIGHLIGHT_OPACITY,
          highlightColors: parsed.highlightColors || DEFAULT_HIGHLIGHT_COLORS,
        });
        console.log("🔧 SettingsPage: Loaded settings:", parsed);
      } catch (e) {
        console.error("❌ SettingsPage: Failed to parse settings:", e);
      }
    }
  }, []);

  const handleOpacityChange = (_: Event, value: number | number[]) => {
    console.log("🔧 SettingsPage: Opacity changed to:", value);
    setSettings({
      ...settings,
      highlightOpacity: value as number,
    });
    setUnsavedChanges(true);
  };

  const handleColorChange = (colorIndex: number, newColor: string) => {
    console.log("🔧 SettingsPage: Color changed:", { colorIndex, newColor });
    const newColors = [...settings.highlightColors];
    newColors[colorIndex] = newColor;
    setSettings({
      ...settings,
      highlightColors: newColors,
    });
    setUnsavedChanges(true);
  };

  const handleAddColor = () => {
    console.log("🔧 SettingsPage: Adding new color");
    setSettings({
      ...settings,
      highlightColors: [...settings.highlightColors, "#ffeb3b"],
    });
    setUnsavedChanges(true);
  };

  const handleRemoveColor = (colorIndex: number) => {
    console.log("🔧 SettingsPage: Removing color at index:", colorIndex);
    if (settings.highlightColors.length > 1) {
      const newColors = settings.highlightColors.filter(
        (_, index) => index !== colorIndex,
      );
      setSettings({
        ...settings,
        highlightColors: newColors,
      });
      setUnsavedChanges(true);
    }
  };

  const handleSave = () => {
    console.log("🔧 SettingsPage: Saving settings");
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    window.dispatchEvent(
      new CustomEvent("settingsChanged", { detail: settings }),
    );
    setUnsavedChanges(false);
    setShowSaveSuccess(true);
  };

  const handleReset = () => {
    console.log("🔧 SettingsPage: Resetting to defaults");
    setSettings({
      highlightOpacity: DEFAULT_HIGHLIGHT_OPACITY,
      highlightColors: [...DEFAULT_HIGHLIGHT_COLORS],
    });
    setUnsavedChanges(true);
  };

  console.log("🔧 SettingsPage: About to render - Step 2");

  try {
    return (
      <Container maxWidth="md" sx={{ py: 4 }}>
        <Paper sx={{ p: 4 }}>
          <Box sx={{ display: "flex", alignItems: "center", mb: 3 }}>
            <IconButton onClick={() => navigate("/")} sx={{ mr: 2 }}>
              <ArrowBack />
            </IconButton>
            <Typography variant="h4" component="h1" sx={{ flexGrow: 1 }}>
              User Settings
            </Typography>
            <Button
              variant="outlined"
              startIcon={<RestoreOutlined />}
              onClick={handleReset}
            >
              Reset to Defaults
            </Button>
          </Box>

          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Customize your viewing preferences. These settings are saved locally
            in your browser.
          </Typography>

          <Divider sx={{ mb: 3 }} />

          <Box sx={{ mb: 4 }}>
            <Typography variant="h6" sx={{ mb: 3 }}>
              PDF Highlighting
            </Typography>

            <FormControl fullWidth sx={{ mb: 3 }}>
              <FormLabel sx={{ mb: 2 }}>
                Highlight Opacity: {Math.round(settings.highlightOpacity * 100)}
                %
              </FormLabel>
              <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
                <Typography variant="body2" color="text.secondary">
                  0%
                </Typography>
                <Slider
                  value={settings.highlightOpacity}
                  onChange={handleOpacityChange}
                  min={0}
                  max={1}
                  step={0.05}
                  marks={[
                    { value: 0, label: "" },
                    { value: 0.25, label: "25%" },
                    { value: 0.5, label: "50%" },
                    { value: 0.75, label: "75%" },
                    { value: 1, label: "100%" },
                  ]}
                  valueLabelDisplay="auto"
                  valueLabelFormat={(value) => `${Math.round(value * 100)}%`}
                />
                <Typography variant="body2" color="text.secondary">
                  100%
                </Typography>
              </Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mt: 1 }}
              >
                Adjust the transparency of highlight overlays on PDF text.
              </Typography>
            </FormControl>

            {/* Color Palette Section */}
            <Box sx={{ mb: 3 }}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  mb: 2,
                }}
              >
                <Typography variant="body1" fontWeight="medium">
                  Highlight Colors
                </Typography>
                <Button
                  size="small"
                  startIcon={<Add />}
                  onClick={handleAddColor}
                  disabled={settings.highlightColors.length >= 12}
                >
                  Add Color
                </Button>
              </Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mb: 2, display: "block" }}
              >
                Click on any color to customize it. Colors are used in order for
                different highlight terms.
              </Typography>
              <Grid container spacing={1}>
                {settings.highlightColors.map((color, index) => (
                  <Grid item key={index}>
                    <Card
                      sx={{
                        width: 80,
                        height: 80,
                        cursor: "pointer",
                        border: 1,
                        borderColor: "divider",
                        position: "relative",
                      }}
                    >
                      <CardContent sx={{ p: 1, "&:last-child": { pb: 1 } }}>
                        <input
                          type="color"
                          value={color}
                          onChange={(e) =>
                            handleColorChange(index, e.target.value)
                          }
                          style={{
                            width: "100%",
                            height: "40px",
                            border: "none",
                            borderRadius: "4px",
                            cursor: "pointer",
                            marginBottom: "4px",
                          }}
                        />
                        <Typography
                          variant="caption"
                          sx={{ fontSize: "0.7rem" }}
                        >
                          #{index + 1}
                        </Typography>
                        {settings.highlightColors.length > 1 && (
                          <IconButton
                            size="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRemoveColor(index);
                            }}
                            sx={{
                              position: "absolute",
                              top: 0,
                              right: 0,
                              p: 0.5,
                            }}
                          >
                            <Delete sx={{ fontSize: "0.8rem" }} />
                          </IconButton>
                        )}
                      </CardContent>
                    </Card>
                  </Grid>
                ))}
              </Grid>
            </Box>

            {/* Preview box */}
            <Paper
              elevation={0}
              sx={{
                p: 2,
                border: 1,
                borderColor: "divider",
                backgroundColor: "background.default",
              }}
            >
              <Typography variant="body2" sx={{ mb: 1 }}>
                Preview:
              </Typography>
              <Typography variant="body1">
                This is some example text with{" "}
                {settings.highlightColors.slice(0, 3).map((color, index) => (
                  <Box
                    key={index}
                    component="span"
                    sx={{
                      backgroundColor: color,
                      opacity: settings.highlightOpacity,
                      padding: "2px 4px",
                      borderRadius: "2px",
                      mr: 0.5,
                    }}
                  >
                    term{index + 1}
                  </Box>
                ))}{" "}
                highlighted to show how they will appear in the PDF viewer.
              </Typography>
            </Paper>
          </Box>

          <Divider sx={{ mb: 3 }} />

          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography variant="caption" color="text.secondary">
              {unsavedChanges
                ? "⚠️ You have unsaved changes"
                : "Opacity settings configured."}
            </Typography>

            <Button
              variant="contained"
              startIcon={<Save />}
              onClick={handleSave}
              disabled={!unsavedChanges}
            >
              Save Settings
            </Button>
          </Box>
        </Paper>

        <Snackbar
          open={showSaveSuccess}
          autoHideDuration={3000}
          onClose={() => setShowSaveSuccess(false)}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <Alert
            onClose={() => setShowSaveSuccess(false)}
            severity="success"
            sx={{ width: "100%" }}
          >
            Settings saved successfully!
          </Alert>
        </Snackbar>
      </Container>
    );
  } catch (error) {
    console.error("❌ SettingsPage: Error in render:", error);
    return (
      <Container maxWidth="md" sx={{ py: 4 }}>
        <Paper sx={{ p: 4 }}>
          <Typography variant="h4" color="error">
            Error in Settings Page
          </Typography>
          <Typography variant="body1">{String(error)}</Typography>
        </Paper>
      </Container>
    );
  }
}

export default SettingsPage;
