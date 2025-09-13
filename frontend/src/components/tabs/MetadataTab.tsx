import { useState } from "react";
import { Box, TextField, Typography, Paper } from "@mui/material";

function MetadataTab() {
  const [metadata, setMetadata] = useState({
    title: "",
    authors: "",
    journal: "",
    year: "",
    doi: "",
    pmid: "",
    abstract: "",
  });

  const handleChange =
    (field: string) => (event: React.ChangeEvent<HTMLInputElement>) => {
      setMetadata({
        ...metadata,
        [field]: event.target.value,
      });
    };

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Paper metadata will be automatically extracted from the PDF. You can
        also edit manually.
      </Typography>

      <Paper sx={{ p: 2 }}>
        <Box sx={{ display: "grid", gap: 2 }}>
          <TextField
            fullWidth
            label="Title"
            value={metadata.title}
            onChange={handleChange("title")}
            multiline
            rows={2}
            size="small"
          />

          <TextField
            fullWidth
            label="Authors"
            value={metadata.authors}
            onChange={handleChange("authors")}
            multiline
            rows={2}
            size="small"
            helperText="Comma-separated list of authors"
          />

          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "2fr 1fr" }}>
            <TextField
              fullWidth
              label="Journal"
              value={metadata.journal}
              onChange={handleChange("journal")}
              size="small"
            />

            <TextField
              fullWidth
              label="Year"
              value={metadata.year}
              onChange={handleChange("year")}
              type="number"
              size="small"
            />
          </Box>

          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "1fr 1fr" }}>
            <TextField
              fullWidth
              label="DOI"
              value={metadata.doi}
              onChange={handleChange("doi")}
              size="small"
            />

            <TextField
              fullWidth
              label="PMID"
              value={metadata.pmid}
              onChange={handleChange("pmid")}
              size="small"
            />
          </Box>

          <TextField
            fullWidth
            label="Abstract"
            value={metadata.abstract}
            onChange={handleChange("abstract")}
            multiline
            rows={4}
            size="small"
          />
        </Box>
      </Paper>
    </Box>
  );
}

export default MetadataTab;
