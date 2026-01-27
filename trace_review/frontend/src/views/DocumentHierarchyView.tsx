import { Box, Typography, Paper, Chip, Alert, Accordion, AccordionSummary, AccordionDetails, List, ListItem, ListItemText, LinearProgress } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import FolderIcon from '@mui/icons-material/Folder';
import DescriptionIcon from '@mui/icons-material/Description';
import WarningIcon from '@mui/icons-material/Warning';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import { DocumentHierarchyData, HierarchySection } from '../types';

interface DocumentHierarchyViewProps {
  data: DocumentHierarchyData;
}

export function DocumentHierarchyView({ data }: DocumentHierarchyViewProps) {
  // Structure type labels and colors
  const getStructureTypeInfo = (type: string) => {
    switch (type) {
      case 'hierarchy':
        return { label: 'Hierarchical', color: 'success' as const, icon: <CheckCircleIcon fontSize="small" /> };
      case 'flat':
        return { label: 'Flat', color: 'info' as const, icon: <DescriptionIcon fontSize="small" /> };
      case 'unresolved':
        return { label: 'Unresolved', color: 'warning' as const, icon: <WarningIcon fontSize="small" /> };
      default:
        return { label: 'Unknown', color: 'default' as const, icon: <ErrorIcon fontSize="small" /> };
    }
  };

  const structureInfo = getStructureTypeInfo(data.structure_type);

  if (!data.found) {
    return (
      <Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3 }}>
          <FolderIcon color="primary" fontSize="large" />
          <Typography variant="h5">Document Hierarchy</Typography>
        </Box>
        <Alert severity="info" sx={{ mb: 2 }}>
          {data.error || 'No PDF specialist found in this trace. The trace may not include PDF document processing.'}
        </Alert>
      </Box>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <FolderIcon color="primary" fontSize="large" />
          <Typography variant="h5">Document Hierarchy</Typography>
        </Box>
        <Chip
          icon={structureInfo.icon}
          label={structureInfo.label}
          color={structureInfo.color}
          size="medium"
          sx={{ fontWeight: 'bold' }}
        />
      </Box>

      {/* Unresolved Warning */}
      {data.structure_type === 'unresolved' && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          <Typography variant="body2" fontWeight="bold">Hierarchy Resolution Failed</Typography>
          <Typography variant="body2">
            The PDF's table of contents could not be extracted. All content is grouped under "Unknown" section.
            This typically happens when the PDF lacks a structured table of contents.
          </Typography>
        </Alert>
      )}

      {/* Document Info */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>Document Information</Typography>

        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
          <Box>
            <Typography variant="caption" color="text.secondary">Document Name</Typography>
            <Typography variant="body1" sx={{ fontWeight: 500 }}>
              {data.document_name || 'N/A'}
            </Typography>
          </Box>

          <Box>
            <Typography variant="caption" color="text.secondary">Total Chunks</Typography>
            <Typography variant="body1" sx={{ fontWeight: 500 }}>
              {data.chunk_count_total}
            </Typography>
          </Box>

          <Box>
            <Typography variant="caption" color="text.secondary">Top-Level Sections</Typography>
            <Typography variant="body1" sx={{ fontWeight: 500 }}>
              {data.top_level_sections.length}
            </Typography>
          </Box>
        </Box>
      </Paper>

      {/* Top-Level Sections List */}
      {data.top_level_sections.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>Top-Level Sections (in order)</Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
            {data.top_level_sections.map((section, idx) => (
              <Chip
                key={idx}
                label={section}
                variant="outlined"
                size="small"
                sx={{ fontWeight: 500 }}
              />
            ))}
          </Box>
        </Paper>
      )}

      {/* Detailed Section Breakdown */}
      {data.sections.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>Section Details</Typography>

          {data.sections.map((section: HierarchySection, idx) => (
            <Accordion key={idx} defaultExpanded={idx === 0}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', justifyContent: 'space-between', pr: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <FolderIcon color="primary" fontSize="small" />
                    <Typography fontWeight={600}>{section.name}</Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    {section.page_range && (
                      <Chip label={`p.${section.page_range}`} size="small" color="info" variant="outlined" />
                    )}
                    <Chip label={`${section.chunk_count} chunks`} size="small" color="secondary" />
                  </Box>
                </Box>
              </AccordionSummary>

              {section.subsections.length > 0 && (
                <AccordionDetails>
                  <List dense disablePadding>
                    {section.subsections.map((sub, subIdx) => (
                      <ListItem key={subIdx} sx={{ pl: 4 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mr: 2 }}>
                          <Typography color="text.secondary">|_</Typography>
                          <DescriptionIcon color="action" fontSize="small" />
                        </Box>
                        <ListItemText
                          primary={sub.name}
                          secondary={
                            <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
                              {sub.page_range && (
                                <Chip label={`p.${sub.page_range}`} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                              )}
                              <Chip label={`${sub.chunk_count} chunks`} size="small" sx={{ height: 20, fontSize: '0.7rem' }} />
                            </Box>
                          }
                        />
                      </ListItem>
                    ))}
                  </List>
                </AccordionDetails>
              )}

              {section.subsections.length === 0 && (
                <AccordionDetails>
                  <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
                    No subsections
                  </Typography>
                </AccordionDetails>
              )}
            </Accordion>
          ))}
        </Paper>
      )}

      {/* Chunk Distribution (visual) */}
      {data.sections.length > 1 && (
        <Paper sx={{ p: 3 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>Chunk Distribution</Typography>
          {data.sections.map((section, idx) => {
            const percentage = data.chunk_count_total > 0
              ? (section.chunk_count / data.chunk_count_total) * 100
              : 0;
            return (
              <Box key={idx} sx={{ mb: 2 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                  <Typography variant="body2">{section.name}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {section.chunk_count} ({percentage.toFixed(1)}%)
                  </Typography>
                </Box>
                <LinearProgress
                  variant="determinate"
                  value={percentage}
                  sx={{ height: 8, borderRadius: 4 }}
                />
              </Box>
            );
          })}
        </Paper>
      )}
    </Box>
  );
}
