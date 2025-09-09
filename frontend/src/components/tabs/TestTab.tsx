import { useState } from "react";
import {
  Box,
  TextField,
  Button,
  Typography,
  Paper,
  Divider,
  Chip,
  Stack,
  Alert,
} from "@mui/material";
import { Search, Clear, Highlight } from "@mui/icons-material";

interface TestTabProps {
  onHighlight?: (searchTerm: string) => void;
  onClearHighlights?: () => void;
}

function TestTab({ onHighlight, onClearHighlights }: TestTabProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [activeHighlights, setActiveHighlights] = useState<string[]>([]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchTerm.trim()) {
      if (onHighlight) {
        onHighlight(searchTerm.trim());
      }
      if (!activeHighlights.includes(searchTerm.trim())) {
        setActiveHighlights([...activeHighlights, searchTerm.trim()]);
      }
      setSearchTerm("");
    }
  };

  const handleClear = () => {
    setActiveHighlights([]);
    if (onClearHighlights) {
      onClearHighlights();
    }
  };

  const handleRemoveHighlight = (term: string) => {
    setActiveHighlights(activeHighlights.filter((h) => h !== term));
    // In a real implementation, we'd also remove just this highlight from the PDF
  };

  return (
    <Box
      sx={{ height: "100%", display: "flex", flexDirection: "column", p: 2 }}
    >
      <Typography variant="h6" gutterBottom>
        PDF Text Highlighting
      </Typography>

      <Alert severity="info" sx={{ mb: 2 }}>
        Enter text to highlight in the PDF. Multiple terms can be highlighted
        simultaneously.
      </Alert>

      <Paper elevation={1} sx={{ p: 2, mb: 2 }}>
        <form onSubmit={handleSubmit}>
          <TextField
            fullWidth
            label="Search Term"
            placeholder="Enter text to highlight..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            variant="outlined"
            size="small"
            sx={{ mb: 2 }}
            InputProps={{
              startAdornment: (
                <Search sx={{ color: "text.secondary", mr: 1 }} />
              ),
            }}
          />

          <Stack direction="row" spacing={1}>
            <Button
              type="submit"
              variant="contained"
              startIcon={<Highlight />}
              disabled={!searchTerm.trim()}
            >
              Highlight
            </Button>

            <Button
              variant="outlined"
              startIcon={<Clear />}
              onClick={handleClear}
              disabled={activeHighlights.length === 0}
            >
              Clear All
            </Button>
          </Stack>
        </form>
      </Paper>

      <Divider sx={{ mb: 2 }} />

      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Active Highlights ({activeHighlights.length})
        </Typography>

        {activeHighlights.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No active highlights
          </Typography>
        ) : (
          <Stack direction="row" flexWrap="wrap" spacing={1} useFlexGap>
            {activeHighlights.map((term, index) => (
              <Chip
                key={index}
                label={term}
                onDelete={() => handleRemoveHighlight(term)}
                color="primary"
                variant="outlined"
                size="small"
              />
            ))}
          </Stack>
        )}
      </Box>

      <Divider sx={{ mt: 3, mb: 2 }} />

      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Quick Highlight Options
        </Typography>

        <Stack spacing={1}>
          <Button
            variant="outlined"
            size="small"
            onClick={() => {
              if (onHighlight) {
                onHighlight("gene");
                setActiveHighlights([...activeHighlights, "gene"]);
              }
            }}
          >
            Highlight "gene"
          </Button>

          <Button
            variant="outlined"
            size="small"
            onClick={() => {
              if (onHighlight) {
                onHighlight("protein");
                setActiveHighlights([...activeHighlights, "protein"]);
              }
            }}
          >
            Highlight "protein"
          </Button>

          <Button
            variant="outlined"
            size="small"
            onClick={() => {
              if (onHighlight) {
                onHighlight("mutation");
                setActiveHighlights([...activeHighlights, "mutation"]);
              }
            }}
          >
            Highlight "mutation"
          </Button>
        </Stack>
      </Box>
    </Box>
  );
}

export default TestTab;
