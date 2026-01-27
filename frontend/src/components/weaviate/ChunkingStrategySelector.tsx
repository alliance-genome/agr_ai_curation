import React, { useState } from 'react';
import {
  Box,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Slider,
  Typography,
  Paper,
  Grid,
  Alert,
  FormHelperText,
  Button,
  Divider,
} from '@mui/material';
import { Info, Save } from '@mui/icons-material';

interface ChunkingStrategy {
  strategyName: 'research' | 'legal' | 'technical' | 'general';
  chunkingMethod: 'by_title' | 'by_paragraph' | 'by_character' | 'by_sentence';
  maxCharacters: number;
  overlapCharacters: number;
  includeMetadata: boolean;
  excludeElementTypes: string[];
}

interface ChunkingStrategySelectorProps {
  initialStrategy?: ChunkingStrategy;
  onSave?: (strategy: ChunkingStrategy) => void;
}

const ChunkingStrategySelector: React.FC<ChunkingStrategySelectorProps> = ({
  initialStrategy = {
    strategyName: 'general',
    chunkingMethod: 'by_paragraph',
    maxCharacters: 1500,
    overlapCharacters: 200,
    includeMetadata: true,
    excludeElementTypes: ['Footer', 'Header'],
  },
  onSave,
}) => {
  const [strategy, setStrategy] = useState<ChunkingStrategy>(initialStrategy);

  const predefinedStrategies: Record<ChunkingStrategy['strategyName'], Partial<ChunkingStrategy>> = {
    research: {
      chunkingMethod: 'by_title',
      maxCharacters: 1500,
      overlapCharacters: 200,
      includeMetadata: true,
      excludeElementTypes: ['Footer', 'Header'],
    },
    legal: {
      chunkingMethod: 'by_paragraph',
      maxCharacters: 1000,
      overlapCharacters: 100,
      includeMetadata: true,
      excludeElementTypes: ['Footer', 'Header'],
    },
    technical: {
      chunkingMethod: 'by_character',
      maxCharacters: 2000,
      overlapCharacters: 400,
      includeMetadata: true,
      excludeElementTypes: ['Footer', 'Header'],
    },
    general: {
      chunkingMethod: 'by_paragraph',
      maxCharacters: 1500,
      overlapCharacters: 200,
      includeMetadata: true,
      excludeElementTypes: ['Footer', 'Header'],
    },
  };

  const handleStrategyChange = (strategyName: ChunkingStrategy['strategyName']) => {
    const predefined = predefinedStrategies[strategyName];
    setStrategy({
      ...strategy,
      strategyName,
      ...predefined,
    });
  };

  const handleSave = () => {
    onSave?.(strategy);
  };

  const getStrategyDescription = (strategyName: string): string => {
    switch (strategyName) {
      case 'research':
        return 'Optimized for academic papers and research documents. Preserves section structure using title-based chunking.';
      case 'legal':
        return 'Designed for legal documents. Maintains paragraph integrity for clause and section preservation.';
      case 'technical':
        return 'Best for technical manuals and documentation. Uses character-based chunking with high overlap for context retention.';
      case 'general':
        return 'Balanced approach suitable for most document types. Uses paragraph-based chunking with moderate overlap.';
      default:
        return '';
    }
  };

  const getMethodDescription = (method: string): string => {
    switch (method) {
      case 'by_title':
        return 'Splits at section boundaries, preserving document structure';
      case 'by_paragraph':
        return 'Maintains paragraph integrity for better context';
      case 'by_character':
        return 'Fixed-size chunks with precise control';
      case 'by_sentence':
        return 'Splits at sentence boundaries for natural breaks';
      default:
        return '';
    }
  };

  return (
    <Box>
      <FormControl fullWidth sx={{ mb: 3 }}>
        <InputLabel>Strategy Preset</InputLabel>
        <Select
          value={strategy.strategyName}
          label="Strategy Preset"
          onChange={(e) => handleStrategyChange(e.target.value as ChunkingStrategy['strategyName'])}
        >
          <MenuItem value="research">Research Papers</MenuItem>
          <MenuItem value="legal">Legal Documents</MenuItem>
          <MenuItem value="technical">Technical Manuals</MenuItem>
          <MenuItem value="general">General Purpose</MenuItem>
        </Select>
        <FormHelperText>{getStrategyDescription(strategy.strategyName)}</FormHelperText>
      </FormControl>

      <Alert severity="info" icon={<Info />} sx={{ mb: 3 }}>
        {getStrategyDescription(strategy.strategyName)}
      </Alert>

      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Typography variant="subtitle2" gutterBottom>
          Configuration Preview
        </Typography>
        <Divider sx={{ my: 1 }} />

        <Grid container spacing={2}>
          <Grid item xs={12}>
            <FormControl fullWidth size="small">
              <InputLabel>Chunking Method</InputLabel>
              <Select
                value={strategy.chunkingMethod}
                label="Chunking Method"
                onChange={(e) =>
                  setStrategy({
                    ...strategy,
                    chunkingMethod: e.target.value as ChunkingStrategy['chunkingMethod'],
                  })
                }
              >
                <MenuItem value="by_title">By Title</MenuItem>
                <MenuItem value="by_paragraph">By Paragraph</MenuItem>
                <MenuItem value="by_character">By Character</MenuItem>
                <MenuItem value="by_sentence">By Sentence</MenuItem>
              </Select>
              <FormHelperText>{getMethodDescription(strategy.chunkingMethod)}</FormHelperText>
            </FormControl>
          </Grid>

          <Grid item xs={12}>
            <Typography variant="body2" gutterBottom>
              Maximum Characters: {strategy.maxCharacters}
            </Typography>
            <Slider
              value={strategy.maxCharacters}
              onChange={(_e, value) =>
                setStrategy({ ...strategy, maxCharacters: value as number })
              }
              min={500}
              max={5000}
              step={100}
              marks={[
                { value: 500, label: '500' },
                { value: 2500, label: '2500' },
                { value: 5000, label: '5000' },
              ]}
            />
            <FormHelperText>
              Maximum size of each chunk in characters
            </FormHelperText>
          </Grid>

          <Grid item xs={12}>
            <Typography variant="body2" gutterBottom>
              Overlap Characters: {strategy.overlapCharacters}
            </Typography>
            <Slider
              value={strategy.overlapCharacters}
              onChange={(_e, value) =>
                setStrategy({ ...strategy, overlapCharacters: value as number })
              }
              min={0}
              max={Math.floor(strategy.maxCharacters / 2)}
              step={50}
              marks={[
                { value: 0, label: '0' },
                {
                  value: Math.floor(strategy.maxCharacters / 2),
                  label: `${Math.floor(strategy.maxCharacters / 2)}`,
                },
              ]}
            />
            <FormHelperText>
              Character overlap between consecutive chunks for context preservation
            </FormHelperText>
          </Grid>

          <Grid item xs={12}>
            <Typography variant="body2" color="text.secondary">
              Excluded Elements: {strategy.excludeElementTypes.join(', ')}
            </Typography>
          </Grid>
        </Grid>
      </Paper>

      <Box sx={{ display: 'flex', gap: 2 }}>
        <Button
          variant="contained"
          startIcon={<Save />}
          onClick={handleSave}
          size="small"
        >
          Save Strategy
        </Button>
      </Box>
    </Box>
  );
};

export default ChunkingStrategySelector;