import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  AppBar,
  Toolbar,
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
  Stack,
} from "@mui/material";
import {
  ArrowBack,
  RestoreOutlined,
  Save,
  Add,
  Delete,
  AccountTree,
  Home as HomeIcon,
  Settings as SettingsIcon,
  Description,
  AdminPanelSettings as AdminIcon,
  Brightness4,
  Brightness7,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";

const SETTINGS_KEY = "alliance-user-settings";
const DEFAULT_HIGHLIGHT_OPACITY = 0.4;
const DEFAULT_HIGHLIGHT_COLORS = [
  "#ffd54f",
  "#80deea",
  "#c5e1a5",
  "#f48fb1",
  "#ce93d8",
  "#90caf9",
  "#ffcc80",
  "#bcaaa4",
];

interface UserSettings {
  highlightOpacity: number;
  highlightColors: string[];
}

interface SettingsPageProps {
  toggleColorMode: () => void;
}

function SettingsPage({ toggleColorMode }: SettingsPageProps) {
  const navigate = useNavigate();
  const theme = useTheme();
  const [settings, setSettings] = useState<UserSettings>({
    highlightOpacity: DEFAULT_HIGHLIGHT_OPACITY,
    highlightColors: [...DEFAULT_HIGHLIGHT_COLORS],
  });
  const [unsavedChanges, setUnsavedChanges] = useState(false);
  const [showSaveSuccess, setShowSaveSuccess] = useState(false);

  useEffect(() => {
    const savedSettings = localStorage.getItem(SETTINGS_KEY);
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings);
        setSettings({
          highlightOpacity:
            typeof parsed.highlightOpacity === "number"
              ? parsed.highlightOpacity
              : DEFAULT_HIGHLIGHT_OPACITY,
          highlightColors: Array.isArray(parsed.highlightColors)
            ? parsed.highlightColors
            : [...DEFAULT_HIGHLIGHT_COLORS],
        });
      } catch (error) {
        console.error("❌ SettingsPage: Failed to parse settings:", error);
      }
    }
  }, []);

  const handleOpacityChange = (_: Event, value: number | number[]) => {
    const opacity = Array.isArray(value) ? value[0] : value;
    setSettings((prev) => ({ ...prev, highlightOpacity: opacity }));
    setUnsavedChanges(true);
  };

  const handleColorChange = (index: number, newColor: string) => {
    setSettings((prev) => {
      const colors = [...prev.highlightColors];
      colors[index] = newColor;
      return { ...prev, highlightColors: colors };
    });
    setUnsavedChanges(true);
  };

  const handleAddColor = () => {
    setSettings((prev) => ({
      ...prev,
      highlightColors: [...prev.highlightColors, "#ffeb3b"],
    }));
    setUnsavedChanges(true);
  };

  const handleRemoveColor = (index: number) => {
    setSettings((prev) => {
      if (prev.highlightColors.length <= 1) {
        return prev;
      }
      const colors = prev.highlightColors.filter((_, i) => i !== index);
      return { ...prev, highlightColors: colors };
    });
    setUnsavedChanges(true);
  };

  const handleSave = () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    window.dispatchEvent(
      new CustomEvent("settingsChanged", { detail: settings }),
    );
    setUnsavedChanges(false);
    setShowSaveSuccess(true);
  };

  const handleReset = () => {
    setSettings({
      highlightOpacity: DEFAULT_HIGHLIGHT_OPACITY,
      highlightColors: [...DEFAULT_HIGHLIGHT_COLORS],
    });
    setUnsavedChanges(true);
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <AppBar position="static">
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
            variant="outlined"
            startIcon={<SettingsIcon />}
            onClick={() => navigate("/settings")}
            sx={{ mr: 1 }}
          >
            Settings
          </Button>
          <Button
            color="inherit"
            startIcon={<Description />}
            onClick={() => navigate("/browser")}
            sx={{ mr: 1 }}
          >
            Browser
          </Button>
          <Button
            color="inherit"
            startIcon={<AccountTree />}
            onClick={() => navigate("/ontology")}
            sx={{ mr: 1 }}
          >
            Ontologies
          </Button>
          <Button
            color="inherit"
            startIcon={<AdminIcon />}
            onClick={() => navigate("/admin")}
          >
            Admin
          </Button>
        </Toolbar>
      </AppBar>

      <Box
        component="main"
        sx={{ width: "100%", flexGrow: 1, bgcolor: "background.default" }}
      >
        <Container maxWidth="md" sx={{ py: 4 }}>
          <Paper sx={{ p: 4 }}>
            <Box sx={{ display: "flex", alignItems: "center", mb: 3 }}>
              <IconButton onClick={() => navigate("/")} sx={{ mr: 2 }}>
                <ArrowBack />
              </IconButton>
              <Typography variant="h4" component="h1" sx={{ flexGrow: 1 }}>
                User Settings
              </Typography>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
                <Button
                  startIcon={<AccountTree />}
                  onClick={() => navigate("/ontology")}
                >
                  Ontologies
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<RestoreOutlined />}
                  onClick={handleReset}
                >
                  Reset to Defaults
                </Button>
              </Stack>
            </Box>

            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
              Customize your viewing preferences. These settings are stored
              locally in your browser.
            </Typography>

            <Divider sx={{ mb: 3 }} />

            <Box sx={{ mb: 4 }}>
              <Typography variant="h6" sx={{ mb: 3 }}>
                PDF Highlighting
              </Typography>

              <FormControl fullWidth sx={{ mb: 3 }}>
                <FormLabel sx={{ mb: 2 }}>
                  Highlight Opacity:{" "}
                  {Math.round(settings.highlightOpacity * 100)}%
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
                  Click on any color to customize it. Colors are used in order
                  for different highlight terms.
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
                            onChange={(event) =>
                              handleColorChange(index, event.target.value)
                            }
                            style={{
                              width: "100%",
                              height: "40px",
                              border: "none",
                              background: "none",
                              cursor: "pointer",
                            }}
                          />
                          <Typography
                            variant="caption"
                            display="block"
                            align="center"
                          >
                            {color}
                          </Typography>
                          <IconButton
                            size="small"
                            onClick={() => handleRemoveColor(index)}
                            sx={{ position: "absolute", top: 4, right: 4 }}
                            disabled={settings.highlightColors.length <= 1}
                          >
                            <Delete fontSize="small" />
                          </IconButton>
                        </CardContent>
                      </Card>
                    </Grid>
                  ))}
                </Grid>
              </Box>

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
                        px: 0.5,
                        borderRadius: 0.5,
                        mr: 0.5,
                      }}
                    >
                      term{index + 1}
                    </Box>
                  ))}{" "}
                  highlighted to show how colors will appear in the PDF viewer.
                </Typography>
              </Paper>
            </Box>

            <Divider sx={{ mb: 3 }} />

            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                flexWrap: "wrap",
                gap: 2,
              }}
            >
              <Typography variant="caption" color="text.secondary">
                {unsavedChanges
                  ? "⚠️ You have unsaved changes"
                  : "Settings are up to date."}
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
        </Container>
      </Box>

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
    </Box>
  );
}

export default SettingsPage;
