import { useState, useEffect } from "react";
import { Box, Chip, Fade, IconButton } from "@mui/material";
import { BugReport, Close } from "@mui/icons-material";
import { debug } from "../utils/debug";

function DebugBadge() {
  const [isDebugEnabled, setIsDebugEnabled] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);

  useEffect(() => {
    // Check initial debug state
    setIsDebugEnabled(debug.isEnabled());

    // Listen for debug mode changes
    const checkDebugMode = () => {
      const enabled = debug.isEnabled();
      setIsDebugEnabled(enabled);
    };

    // Check periodically for changes (since we don't have an event system)
    const interval = setInterval(checkDebugMode, 1000);

    return () => clearInterval(interval);
  }, []);

  if (!isDebugEnabled) {
    return null;
  }

  if (isMinimized) {
    return (
      <Fade in timeout={300}>
        <IconButton
          onClick={() => setIsMinimized(false)}
          sx={{
            position: "fixed",
            bottom: 16,
            right: 16,
            zIndex: 9999,
            backgroundColor: "warning.main",
            color: "warning.contrastText",
            "&:hover": {
              backgroundColor: "warning.dark",
            },
            boxShadow: 3,
          }}
          size="small"
        >
          <BugReport fontSize="small" />
        </IconButton>
      </Fade>
    );
  }

  return (
    <Fade in timeout={300}>
      <Box
        sx={{
          position: "fixed",
          bottom: 20,
          right: 20,
          zIndex: 9999,
          display: "flex",
          alignItems: "center",
          gap: 1,
        }}
      >
        <Chip
          icon={<BugReport />}
          label="Debug Mode Active"
          color="warning"
          variant="filled"
          onDelete={() => setIsMinimized(true)}
          deleteIcon={<Close />}
          sx={{
            boxShadow: 4,
            fontWeight: "bold",
            fontSize: "0.875rem",
          }}
        />
      </Box>
    </Fade>
  );
}

export default DebugBadge;
