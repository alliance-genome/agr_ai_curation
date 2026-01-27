import React, { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Stack,
  IconButton,
  Divider,
  Grid,
  Tooltip,
} from '@mui/material';
import {
  ExpandMore,
  ExpandLess,
  Description,
  TableChart,
  Image,
  FormatListBulleted,
} from '@mui/icons-material';

interface DocumentChunk {
  id: string;
  documentId: string;
  chunkIndex: number;
  content: string;
  elementType: string;
  pageNumber: number;
  sectionTitle?: string;
  metadata: {
    characterCount: number;
    wordCount: number;
    hasTable: boolean;
    hasImage: boolean;
  };
}

interface ChunkPreviewProps {
  chunks: DocumentChunk[];
  maxPreviewLength?: number;
  showMetadata?: boolean;
}

const ChunkPreview: React.FC<ChunkPreviewProps> = ({
  chunks,
  maxPreviewLength = 300,
  showMetadata = true,
}) => {
  const [expandedChunks, setExpandedChunks] = useState<Set<string>>(new Set());

  const toggleChunkExpansion = (chunkId: string) => {
    setExpandedChunks((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(chunkId)) {
        newSet.delete(chunkId);
      } else {
        newSet.add(chunkId);
      }
      return newSet;
    });
  };

  const getElementIcon = (elementType: string) => {
    switch (elementType) {
      case 'Table':
        return <TableChart fontSize="small" />;
      case 'Image':
        return <Image fontSize="small" />;
      case 'ListItem':
        return <FormatListBulleted fontSize="small" />;
      default:
        return <Description fontSize="small" />;
    }
  };

  const getElementColor = (elementType: string): 'default' | 'primary' | 'secondary' | 'info' | 'warning' => {
    switch (elementType) {
      case 'Title':
        return 'primary';
      case 'Table':
        return 'info';
      case 'Image':
        return 'warning';
      case 'ListItem':
        return 'secondary';
      default:
        return 'default';
    }
  };

  const truncateContent = (content: string, maxLength: number): string => {
    if (content.length <= maxLength) return content;
    return content.substring(0, maxLength) + '...';
  };

  if (chunks.length === 0) {
    return (
      <Box sx={{ textAlign: 'center', py: 4 }}>
        <Typography variant="body2" color="text.secondary">
          No chunks available for preview
        </Typography>
      </Box>
    );
  }

  return (
    <Stack spacing={2}>
      {chunks.map((chunk) => {
        const isExpanded = expandedChunks.has(chunk.id);
        const shouldTruncate = chunk.content.length > maxPreviewLength;

        return (
          <Card key={chunk.id} variant="outlined">
            <CardContent>
              <Box sx={{ mb: 2 }}>
                <Grid container alignItems="center" spacing={1}>
                  <Grid item xs>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="subtitle2" component="span">
                        Chunk #{chunk.chunkIndex}
                      </Typography>
                      {chunk.sectionTitle && (
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          component="span"
                        >
                          • {chunk.sectionTitle}
                        </Typography>
                      )}
                    </Stack>
                  </Grid>
                  <Grid item>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Chip
                        icon={getElementIcon(chunk.elementType)}
                        label={chunk.elementType}
                        size="small"
                        color={getElementColor(chunk.elementType)}
                        variant="outlined"
                      />
                      <Chip
                        label={`Page ${chunk.pageNumber}`}
                        size="small"
                        variant="outlined"
                      />
                      {shouldTruncate && (
                        <IconButton
                          size="small"
                          onClick={() => toggleChunkExpansion(chunk.id)}
                        >
                          {isExpanded ? <ExpandLess /> : <ExpandMore />}
                        </IconButton>
                      )}
                    </Stack>
                  </Grid>
                </Grid>
              </Box>

              <Box sx={{ mb: showMetadata ? 2 : 0 }}>
                <Typography
                  variant="body2"
                  sx={{
                    whiteSpace: isExpanded ? 'pre-wrap' : 'normal',
                    wordBreak: 'break-word',
                  }}
                >
                  {isExpanded
                    ? chunk.content
                    : truncateContent(chunk.content, maxPreviewLength)}
                </Typography>
              </Box>

              {showMetadata && (
                <>
                  <Divider sx={{ my: 1 }} />
                  <Grid container spacing={2}>
                    <Grid item>
                      <Typography variant="caption" color="text.secondary">
                        Characters: {chunk.metadata.characterCount.toLocaleString()}
                      </Typography>
                    </Grid>
                    <Grid item>
                      <Typography variant="caption" color="text.secondary">
                        Words: {chunk.metadata.wordCount.toLocaleString()}
                      </Typography>
                    </Grid>
                    {chunk.metadata.hasTable && (
                      <Grid item>
                        <Tooltip title="Contains table">
                          <Chip
                            icon={<TableChart />}
                            label="Table"
                            size="small"
                            variant="filled"
                            color="info"
                          />
                        </Tooltip>
                      </Grid>
                    )}
                    {chunk.metadata.hasImage && (
                      <Grid item>
                        <Tooltip title="Contains image">
                          <Chip
                            icon={<Image />}
                            label="Image"
                            size="small"
                            variant="filled"
                            color="warning"
                          />
                        </Tooltip>
                      </Grid>
                    )}
                  </Grid>
                </>
              )}
            </CardContent>
          </Card>
        );
      })}
    </Stack>
  );
};

export default ChunkPreview;