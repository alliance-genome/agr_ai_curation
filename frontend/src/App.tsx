import { ThemeProvider, createTheme, CssBaseline } from "@mui/material";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { useMemo, useState, useEffect } from "react";
import HomePage from "./pages/HomePage";
import AdminPage from "./pages/AdminPage";
import SettingsPage from "./pages/SettingsPage";
import DebugBadge from "./components/DebugBadge";
import { debug } from "./utils/debug";
import axios from "axios";

function App() {
  console.log("🚀 APP: App component function called/rendered");
  const [mode, setMode] = useState<"light" | "dark">("dark");

  useEffect(() => {
    const savedMode = localStorage.getItem("themeMode") as
      | "light"
      | "dark"
      | null;
    if (savedMode) {
      setMode(savedMode);
    }

    // Initialize debug mode from settings
    const initDebugMode = async () => {
      try {
        const response = await axios.get("/api/settings");
        if (response.data.debug_mode) {
          debug.setEnabled(true);
          debug.log("APP", "Debug mode enabled from settings");
        }
      } catch (error) {
        // If settings fail to load, check localStorage
        const localDebug = localStorage.getItem("debug_mode") === "true";
        if (localDebug) {
          debug.setEnabled(true);
          debug.log("APP", "Debug mode enabled from localStorage");
        }
      }
    };

    initDebugMode();
  }, []);

  const theme = useMemo(
    () =>
      createTheme({
        palette: {
          mode,
          primary: {
            main: "#2196f3",
          },
          secondary: {
            main: "#f50057",
          },
          background: {
            default: mode === "dark" ? "#121212" : "#f5f5f5",
            paper: mode === "dark" ? "#1e1e1e" : "#ffffff",
          },
        },
        typography: {
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
        },
        shape: {
          borderRadius: 8,
        },
        components: {
          MuiButton: {
            styleOverrides: {
              root: {
                textTransform: "none",
              },
            },
          },
          MuiCard: {
            styleOverrides: {
              root: {
                borderRadius: 12,
              },
            },
          },
        },
      }),
    [mode],
  );

  const toggleColorMode = () => {
    const newMode = mode === "light" ? "dark" : "light";
    setMode(newMode);
    localStorage.setItem("themeMode", newMode);
  };

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <Routes>
          <Route
            path="/"
            element={
              <>
                {console.log("🚀 APP: Rendering HomePage route")}
                <HomePage toggleColorMode={toggleColorMode} />
              </>
            }
          />
          <Route
            path="/admin"
            element={<AdminPage toggleColorMode={toggleColorMode} />}
          />
          <Route
            path="/settings"
            element={
              <>
                {console.log("🔧 App: Rendering SettingsPage route")}
                <SettingsPage />
              </>
            }
          />
        </Routes>
      </BrowserRouter>
      <DebugBadge />
    </ThemeProvider>
  );
}

export default App;
