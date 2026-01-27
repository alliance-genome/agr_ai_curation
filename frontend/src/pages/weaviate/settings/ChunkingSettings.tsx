import React, { useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Button,
  Slider,
} from '@mui/material';

const ChunkingSettings: React.FC = () => {
  const [chunkSize, setChunkSize] = useState(500);
  const [overlap, setOverlap] = useState(50);
  const [strategy, setStrategy] = useState('sentence');

  const handleSave = () => {
    // TODO: Implement save functionality
    console.log('Saving chunking settings:', { chunkSize, overlap, strategy });
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Chunking Settings
      </Typography>

      <Paper sx={{ p: 3, mt: 3 }}>
        <Typography variant="h6" gutterBottom>
          Document Chunking Configuration
        </Typography>

        <FormControl fullWidth sx={{ mt: 2 }}>
          <InputLabel>Chunking Strategy</InputLabel>
          <Select
            value={strategy}
            label="Chunking Strategy"
            onChange={(e) => setStrategy(e.target.value)}
          >
            <MenuItem value="sentence">Sentence-based</MenuItem>
            <MenuItem value="fixed">Fixed Size</MenuItem>
            <MenuItem value="paragraph">Paragraph-based</MenuItem>
            <MenuItem value="semantic">Semantic</MenuItem>
          </Select>
        </FormControl>

        <Box sx={{ mt: 3 }}>
          <Typography gutterBottom>
            Chunk Size: {chunkSize} tokens
          </Typography>
          <Slider
            value={chunkSize}
            onChange={(_, value) => setChunkSize(value as number)}
            min={100}
            max={2000}
            step={50}
            marks={[
              { value: 100, label: '100' },
              { value: 500, label: '500' },
              { value: 1000, label: '1000' },
              { value: 2000, label: '2000' },
            ]}
          />
        </Box>

        <Box sx={{ mt: 3 }}>
          <Typography gutterBottom>
            Overlap: {overlap} tokens
          </Typography>
          <Slider
            value={overlap}
            onChange={(_, value) => setOverlap(value as number)}
            min={0}
            max={200}
            step={10}
            marks={[
              { value: 0, label: '0' },
              { value: 50, label: '50' },
              { value: 100, label: '100' },
              { value: 200, label: '200' },
            ]}
          />
        </Box>

        <Box sx={{ mt: 4, display: 'flex', gap: 2 }}>
          <Button variant="contained" onClick={handleSave}>
            Save Changes
          </Button>
          <Button variant="outlined">
            Cancel
          </Button>
        </Box>

        <Box sx={{ mt: 3, p: 2, bgcolor: 'background.default', borderRadius: 1 }}>
          <Typography variant="body2" color="text.secondary">
            <strong>Note:</strong> Changes to chunking settings will only affect new documents.
            Existing documents will need to be reprocessed to apply new chunking settings.
          </Typography>
        </Box>
      </Paper>
    </Box>
  );
};

export default ChunkingSettings;