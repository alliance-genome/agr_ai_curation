import { Box, Typography, Paper, Chip, Alert, Divider, Accordion, AccordionSummary, AccordionDetails } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import DescriptionIcon from '@mui/icons-material/Description';
import SearchIcon from '@mui/icons-material/Search';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import { PDFCitationsData } from '../types';

interface PDFCitationsViewProps {
  data: PDFCitationsData;
}

export function PDFCitationsView({ data }: PDFCitationsViewProps) {
  if (!data.found) {
    return (
      <Box>
        <Typography variant="h5" gutterBottom>
          PDF Citations
        </Typography>
        <Alert severity="info">No PDF citations found in this trace</Alert>
      </Box>
    );
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3 }}>
        <DescriptionIcon color="primary" fontSize="large" />
        <Typography variant="h5">PDF Citations</Typography>
      </Box>

      {/* Statistics */}
      <Box sx={{ display: 'flex', gap: 2, mb: 3, flexWrap: 'wrap' }}>
        <Chip
          icon={<MenuBookIcon />}
          label={`${data.total_citations} citation${data.total_citations !== 1 ? 's' : ''}`}
          color="primary"
          sx={{ fontWeight: 'bold' }}
        />
        <Chip
          label={`${data.total_chunks_found} chunks found`}
          color="secondary"
        />
        <Chip
          icon={<SearchIcon />}
          label={`${data.search_queries.length} search quer${data.search_queries.length !== 1 ? 'ies' : 'y'}`}
          color="info"
        />
        {data.tool_calls?.length > 0 && (
          <Chip
            label={`${data.tool_calls.length} PDF tool call${data.tool_calls.length !== 1 ? 's' : ''}`}
            variant="outlined"
          />
        )}
      </Box>

      {/* Search Queries Used */}
      {data.search_queries.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
            <SearchIcon color="primary" fontSize="small" />
            Search Queries
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {data.search_queries.map((query, index) => (
              <Box
                key={index}
                sx={{
                  display: 'flex',
                  gap: 2,
                  alignItems: 'flex-start'
                }}
              >
                <Chip
                  label={index + 1}
                  size="small"
                  color="primary"
                  sx={{ minWidth: 28, fontWeight: 'bold' }}
                />
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    lineHeight: 1.6,
                    color: 'text.primary',
                    backgroundColor: 'rgba(33, 150, 243, 0.05)',
                    p: 1.5,
                    borderRadius: 1,
                    borderLeft: '3px solid',
                    borderLeftColor: 'primary.main'
                  }}
                >
                  {query}
                </Typography>
              </Box>
            ))}
          </Box>
        </Paper>
      )}

      {/* Extracted Content (Answer from PDF Specialist) */}
      {data.extracted_content && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>
            Extracted Answer
          </Typography>
          <Box
            sx={{
              whiteSpace: 'pre-wrap',
              backgroundColor: 'rgba(33, 150, 243, 0.08)',
              p: 2,
              borderRadius: 1,
              borderLeft: '4px solid',
              borderLeftColor: 'primary.main',
              fontFamily: 'inherit',
              lineHeight: 1.7
            }}
          >
            <Typography variant="body1" component="div">
              {data.extracted_content}
            </Typography>
          </Box>
        </Paper>
      )}

      {/* Citations with Page Numbers and Section Titles */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
          <MenuBookIcon color="primary" fontSize="small" />
          Citations ({data.total_citations})
        </Typography>

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {data.citations.map((citation, index) => (
            <Box key={index}>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 2,
                  flexWrap: 'wrap',
                  p: 2,
                  borderRadius: 1,
                  backgroundColor: 'rgba(255, 255, 255, 0.03)',
                  border: '1px solid',
                  borderColor: 'divider'
                }}
              >
                {/* Page number chip */}
                <Chip
                  label={`Page ${citation.page_number}`}
                  size="small"
                  color="warning"
                  sx={{ fontWeight: 'bold' }}
                />

                {/* Section title */}
                {citation.section_title && (
                  <Typography
                    variant="body2"
                    sx={{
                      fontWeight: 500,
                      color: 'text.primary',
                      flex: 1
                    }}
                  >
                    {citation.section_title}
                  </Typography>
                )}

                {/* Chunk ID (if available) */}
                {citation.chunk_id && (
                  <Typography
                    variant="caption"
                    sx={{
                      color: 'text.secondary',
                      fontFamily: 'monospace',
                      backgroundColor: 'rgba(255, 255, 255, 0.05)',
                      px: 1,
                      py: 0.5,
                      borderRadius: 0.5
                    }}
                  >
                    {citation.chunk_id}
                  </Typography>
                )}

                {/* Source (if available) */}
                {citation.source && (
                  <Chip
                    label={citation.source}
                    size="small"
                    variant="outlined"
                    color="secondary"
                  />
                )}
              </Box>

              {/* Divider between citations */}
              {index < data.citations.length - 1 && <Divider sx={{ mt: 2 }} />}
            </Box>
          ))}
        </Box>
      </Paper>

      {/* Tool Calls Metadata (collapsible) */}
      {data.tool_calls && data.tool_calls.length > 0 && (
        <Accordion sx={{ backgroundColor: 'rgba(255, 255, 255, 0.02)' }}>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="subtitle2" color="text.secondary">
              PDF Tool Calls ({data.tool_calls.length})
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {data.tool_calls.map((tc, index) => (
                <Box
                  key={index}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 2,
                    flexWrap: 'wrap'
                  }}
                >
                  <Chip
                    label={tc.tool_name}
                    size="small"
                    color="primary"
                    variant="outlined"
                  />
                  <Typography
                    variant="body2"
                    sx={{
                      flex: 1,
                      fontStyle: tc.query ? 'normal' : 'italic',
                      color: tc.query ? 'text.primary' : 'text.secondary'
                    }}
                  >
                    {tc.query || 'No query'}
                  </Typography>
                  <Chip
                    label={`${tc.citations_count} citation${tc.citations_count !== 1 ? 's' : ''}`}
                    size="small"
                    color="secondary"
                    variant="outlined"
                  />
                </Box>
              ))}
            </Box>
          </AccordionDetails>
        </Accordion>
      )}
    </Box>
  );
}
