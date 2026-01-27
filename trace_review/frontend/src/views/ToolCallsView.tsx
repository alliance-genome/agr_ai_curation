import { useState } from 'react';
import {
  Box,
  Typography,
  Paper,
  Chip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Button,
  ButtonGroup,
  Collapse,
  Grid,
  Divider
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess';
import CodeIcon from '@mui/icons-material/Code';
import InputIcon from '@mui/icons-material/Input';
import OutputIcon from '@mui/icons-material/Output';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import Tooltip from '@mui/material/Tooltip';
import { ToolCallsData, ToolResultParsed } from '../types';

interface ToolCallsViewProps {
  data: ToolCallsData;
}

// Component for displaying formatted arguments
function FormattedArguments({ args }: { args: any }) {
  if (!args || typeof args !== 'object') {
    return <Typography variant="body2" color="text.secondary" fontStyle="italic">No arguments</Typography>;
  }

  const entries = Object.entries(args).filter(([key]) => key !== 'raw');

  if (entries.length === 0) {
    return <Typography variant="body2" color="text.secondary" fontStyle="italic">No arguments</Typography>;
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {entries.map(([key, value]) => (
        <Box key={key} sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
          <Typography
            variant="body2"
            sx={{
              fontWeight: 600,
              color: 'primary.main',
              minWidth: '120px',
              fontFamily: 'monospace'
            }}
          >
            {key}:
          </Typography>
          <Typography
            variant="body2"
            sx={{
              flex: 1,
              wordBreak: 'break-word',
              fontFamily: value === null ? 'inherit' : 'monospace'
            }}
          >
            {value === null ? (
              <em style={{ color: '#888' }}>null</em>
            ) : typeof value === 'object' ? (
              JSON.stringify(value, null, 2)
            ) : (
              String(value)
            )}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

// Component for collapsible raw JSON
function CollapsibleJson({ data, label }: { data: any; label: string }) {
  const [expanded, setExpanded] = useState(false);

  if (!data) return null;

  return (
    <Box sx={{ mt: 2 }}>
      <Button
        size="small"
        startIcon={<CodeIcon />}
        onClick={() => setExpanded(!expanded)}
        sx={{ textTransform: 'none', color: 'text.secondary' }}
      >
        {expanded ? 'Hide' : 'Show'} {label}
      </Button>
      <Collapse in={expanded}>
        <Box
          sx={{
            fontFamily: 'monospace',
            fontSize: '0.75rem',
            backgroundColor: 'rgba(0, 0, 0, 0.2)',
            p: 2,
            borderRadius: 1,
            mt: 1,
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: '300px',
            overflow: 'auto'
          }}
        >
          {typeof data === 'string' ? data : JSON.stringify(data, null, 2)}
        </Box>
      </Collapse>
    </Box>
  );
}

// Component to display parsed chunk hits nicely
function ChunkHitsList({ hits }: { hits: Array<{ chunk_id: string; section_title: string; page_number: number; score: number; content: string }> }) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {hits.map((hit, index) => (
        <Box
          key={hit.chunk_id || index}
          sx={{
            p: 2,
            borderRadius: 1,
            backgroundColor: 'rgba(255, 255, 255, 0.03)',
            border: '1px solid',
            borderColor: 'divider'
          }}
        >
          <Box sx={{ display: 'flex', gap: 1, mb: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Chip label={`Page ${hit.page_number}`} size="small" color="warning" />
            <Chip label={`${(hit.score * 100).toFixed(0)}% match`} size="small" color="success" variant="outlined" />
            <Typography variant="body2" sx={{ fontWeight: 500, flex: 1 }}>
              {hit.section_title}
            </Typography>
          </Box>
          <Typography
            variant="body2"
            sx={{
              color: 'text.secondary',
              fontStyle: 'italic',
              whiteSpace: 'pre-wrap',
              maxHeight: '80px',
              overflow: 'hidden',
              textOverflow: 'ellipsis'
            }}
          >
            "{hit.content}"
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

// Component to display parsed data array (from agr_curation_query)
function DataList({ data }: { data: Array<Record<string, any>> }) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {data.map((item, index) => (
        <Box
          key={index}
          sx={{
            p: 2,
            borderRadius: 1,
            backgroundColor: 'rgba(255, 255, 255, 0.03)',
            border: '1px solid',
            borderColor: 'divider'
          }}
        >
          {Object.entries(item).map(([key, value]) => (
            <Box key={key} sx={{ display: 'flex', gap: 2, mb: 0.5 }}>
              <Typography variant="body2" sx={{ fontWeight: 600, color: 'success.main', minWidth: '100px', fontFamily: 'monospace' }}>
                {key}:
              </Typography>
              <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>
                {String(value)}
              </Typography>
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  );
}

// Component to display section content (from read_section)
function SectionContentDisplay({ section }: { section: { section_title: string; page_numbers: number[]; chunk_count: number; content_preview: string; full_content?: string } }) {
  // Use full_content if available, otherwise fall back to content_preview
  const content = section.full_content || section.content_preview;
  const contentLength = content?.length || 0;

  return (
    <Box
      sx={{
        p: 2,
        borderRadius: 1,
        backgroundColor: 'rgba(255, 255, 255, 0.03)',
        border: '1px solid',
        borderColor: 'divider'
      }}
    >
      <Box sx={{ display: 'flex', gap: 1, mb: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <Typography variant="body2" sx={{ fontWeight: 600, color: 'primary.main' }}>
          {section.section_title}
        </Typography>
        {section.page_numbers?.length > 0 && (
          <Chip label={`Pages ${section.page_numbers.join(', ')}`} size="small" color="warning" />
        )}
        {section.chunk_count && (
          <Chip label={`${section.chunk_count} chunks`} size="small" color="info" variant="outlined" />
        )}
        {contentLength > 0 && (
          <Chip label={`${contentLength.toLocaleString()} chars`} size="small" variant="outlined" />
        )}
      </Box>
      {content && (
        <Typography
          component="pre"
          sx={{
            color: 'text.secondary',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: '500px',
            overflow: 'auto',
            mt: 1,
            p: 2,
            backgroundColor: 'rgba(0, 0, 0, 0.15)',
            borderRadius: 1,
            fontSize: '0.85rem',
            fontFamily: 'inherit',
            margin: 0
          }}
        >
          {content}
        </Typography>
      )}
    </Box>
  );
}

// Component to display JSON data
function JsonDataDisplay({ data }: { data: Record<string, any> | any[] }) {
  if (Array.isArray(data)) {
    return <DataList data={data} />;
  }

  return (
    <Box
      sx={{
        p: 2,
        borderRadius: 1,
        backgroundColor: 'rgba(255, 255, 255, 0.03)',
        border: '1px solid',
        borderColor: 'divider'
      }}
    >
      {Object.entries(data).map(([key, value]) => (
        <Box key={key} sx={{ display: 'flex', gap: 2, mb: 0.5 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, color: 'info.main', minWidth: '100px', fontFamily: 'monospace' }}>
            {key}:
          </Typography>
          <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>
            {typeof value === 'object' ? JSON.stringify(value) : String(value)}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

// Format the tool result nicely with summary, details, and raw views
function FormattedToolResult({ result, resultLength }: { result: ToolResultParsed | null; resultLength: number }) {
  const [showDetails, setShowDetails] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  if (!result) {
    return <Typography variant="body2" color="text.secondary" fontStyle="italic">No result captured</Typography>;
  }

  const { summary, parsed, raw, parse_status } = result;
  const hasHits = parsed?.hits && parsed.hits.length > 0;
  const hasData = parsed?.data && parsed.data.length > 0;
  const hasSection = parsed?.section && parsed.section.section_title;
  const hasJsonData = parsed?.json_data;
  const hasParsedContent = hasHits || hasData || hasSection || hasJsonData;

  // Check if parsing needs attention
  const needsParsingAttention = parse_status === 'unparsed' || parse_status === 'partial';
  const parseWarningMessage = parse_status === 'unparsed'
    ? 'Could not parse this result - click Show Raw to see full output'
    : 'Partial parse - some data may not be displayed';

  // Generate details button label
  const getDetailsLabel = () => {
    if (hasHits) return `${parsed!.hits!.length} hits`;
    if (hasData) return `${parsed!.data!.length} items`;
    if (hasSection) return 'section content';
    if (hasJsonData) return 'JSON data';
    return 'details';
  };

  return (
    <Box>
      {/* Summary - always visible */}
      <Box
        sx={{
          backgroundColor: needsParsingAttention ? 'rgba(255, 152, 0, 0.08)' : 'rgba(76, 175, 80, 0.08)',
          p: 2,
          borderRadius: 1,
          borderLeft: '4px solid',
          borderLeftColor: needsParsingAttention ? 'warning.main' : 'success.main',
          mb: 1
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 500, flex: 1 }}>
            {summary}
          </Typography>
          {needsParsingAttention && (
            <Tooltip title={parseWarningMessage} arrow>
              <Chip
                icon={<WarningAmberIcon />}
                label={parse_status === 'unparsed' ? 'Unparsed' : 'Partial'}
                size="small"
                color="warning"
                variant="outlined"
                sx={{ fontWeight: 600 }}
              />
            </Tooltip>
          )}
        </Box>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
          {resultLength.toLocaleString()} chars total
        </Typography>
      </Box>

      {/* Action buttons */}
      <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
        {hasParsedContent && (
          <Button
            size="small"
            variant={showDetails ? "contained" : "outlined"}
            onClick={() => setShowDetails(!showDetails)}
            sx={{ textTransform: 'none' }}
          >
            {showDetails ? 'Hide Details' : `Show Details (${getDetailsLabel()})`}
          </Button>
        )}
        <Button
          size="small"
          startIcon={<CodeIcon />}
          variant={showRaw ? "contained" : "outlined"}
          onClick={() => setShowRaw(!showRaw)}
          sx={{ textTransform: 'none' }}
        >
          {showRaw ? 'Hide Raw' : 'Show Raw'}
        </Button>
      </Box>

      {/* Parsed details view */}
      <Collapse in={showDetails}>
        <Box sx={{ mt: 2 }}>
          {hasHits && <ChunkHitsList hits={parsed!.hits!} />}
          {hasData && <DataList data={parsed!.data!} />}
          {hasSection && <SectionContentDisplay section={parsed!.section!} />}
          {hasJsonData && <JsonDataDisplay data={parsed!.json_data!} />}
        </Box>
      </Collapse>

      {/* Raw view */}
      <Collapse in={showRaw}>
        <Box
          sx={{
            fontFamily: 'monospace',
            fontSize: '0.75rem',
            backgroundColor: 'rgba(0, 0, 0, 0.2)',
            p: 2,
            borderRadius: 1,
            mt: 2,
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: '400px',
            overflow: 'auto'
          }}
        >
          {raw}
        </Box>
      </Collapse>
    </Box>
  );
}

export function ToolCallsView({ data }: ToolCallsViewProps) {
  const [expandedPanels, setExpandedPanels] = useState<Set<string>>(
    new Set(data.tool_calls[0]?.id ? [data.tool_calls[0].id] : [])
  );

  const getStatusColor = (status: string): "success" | "error" | "warning" | "default" => {
    const s = status?.toLowerCase() || '';
    if (s === 'completed' || s === 'ok' || s === 'success') return 'success';
    if (s === 'error' || s === 'failed') return 'error';
    if (s === 'pending' || s === 'in_progress') return 'warning';
    return 'default';
  };

  const handleExpandAll = () => {
    setExpandedPanels(new Set(data.tool_calls.map(call => call.id)));
  };

  const handleCollapseAll = () => {
    setExpandedPanels(new Set());
  };

  const handlePanelChange = (panelId: string) => (_event: React.SyntheticEvent, isExpanded: boolean) => {
    setExpandedPanels(prev => {
      const newSet = new Set(prev);
      if (isExpanded) {
        newSet.add(panelId);
      } else {
        newSet.delete(panelId);
      }
      return newSet;
    });
  };

  // Check if any tool has URL or status_code (for external API calls)
  const hasExternalCalls = data.tool_calls.some(
    call => (call.url && call.url !== 'N/A') || (call.status_code && call.status_code !== 'N/A')
  );

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h5">Tool Calls</Typography>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
          <ButtonGroup size="small" variant="outlined">
            <Button onClick={handleExpandAll} startIcon={<UnfoldMoreIcon />}>
              Expand All
            </Button>
            <Button onClick={handleCollapseAll} startIcon={<UnfoldLessIcon />}>
              Collapse All
            </Button>
          </ButtonGroup>
          <Chip label={`Total: ${data.total_count}`} color="primary" />
        </Box>
      </Box>

      {/* Unique tools summary */}
      {data.unique_tools.length > 0 && (
        <Box sx={{ mb: 3, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <Typography variant="body2" color="text.secondary" sx={{ mr: 1 }}>
            Tools used:
          </Typography>
          {data.unique_tools.map(tool => (
            <Chip key={tool} label={tool} size="small" variant="outlined" />
          ))}
        </Box>
      )}

      {data.tool_calls.length === 0 ? (
        <Paper sx={{ p: 3 }}>
          <Typography color="text.secondary" fontStyle="italic">
            No tool calls found in this trace
          </Typography>
        </Paper>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {data.tool_calls.map((call, index) => (
            <Accordion
              key={call.id}
              expanded={expandedPanels.has(call.id)}
              onChange={handlePanelChange(call.id)}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flex: 1, flexWrap: 'wrap' }}>
                  <Typography variant="body2" color="text.secondary" sx={{ minWidth: '30px' }}>
                    #{index + 1}
                  </Typography>
                  <Chip
                    label={call.name}
                    size="small"
                    color="primary"
                    sx={{ fontWeight: 600 }}
                  />
                  {call.model && call.model !== 'N/A' && (
                    <Chip
                      label={call.model}
                      size="small"
                      variant="outlined"
                      color="secondary"
                      sx={{ fontFamily: 'monospace' }}
                    />
                  )}
                  {call.duration && call.duration !== 'N/A' && (
                    <Chip
                      label={call.duration}
                      size="small"
                      variant="outlined"
                      sx={{ fontFamily: 'monospace' }}
                    />
                  )}
                  {call.status && call.status !== 'N/A' && (
                    <Chip
                      label={call.status}
                      size="small"
                      color={getStatusColor(call.status)}
                    />
                  )}
                </Box>
              </AccordionSummary>
              <AccordionDetails>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {/* Metadata row */}
                  <Grid container spacing={3}>
                    <Grid item xs={12} sm={6} md={3}>
                      <Typography variant="caption" color="text.secondary">Timestamp</Typography>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                        {call.time ? new Date(call.time).toLocaleString() : 'N/A'}
                      </Typography>
                    </Grid>
                    {call.duration && call.duration !== 'N/A' && (
                      <Grid item xs={6} sm={3} md={2}>
                        <Typography variant="caption" color="text.secondary">Duration</Typography>
                        <Typography variant="body2" sx={{ fontFamily: 'monospace', fontWeight: 600 }}>
                          {call.duration}
                        </Typography>
                      </Grid>
                    )}
                    {call.model && call.model !== 'N/A' && (
                      <Grid item xs={6} sm={3} md={2}>
                        <Typography variant="caption" color="text.secondary">Model</Typography>
                        <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'secondary.main' }}>
                          {call.model}
                        </Typography>
                      </Grid>
                    )}
                    {call.status && call.status !== 'N/A' && (
                      <Grid item xs={6} sm={3} md={2}>
                        <Box>
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                            Status
                          </Typography>
                          <Chip
                            label={call.status}
                            size="small"
                            color={getStatusColor(call.status)}
                          />
                        </Box>
                      </Grid>
                    )}
                    {/* Only show URL/status_code if there are external calls */}
                    {hasExternalCalls && call.url && call.url !== 'N/A' && (
                      <Grid item xs={12}>
                        <Typography variant="caption" color="text.secondary">URL</Typography>
                        <Typography variant="body2" sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
                          {call.url}
                        </Typography>
                      </Grid>
                    )}
                    {hasExternalCalls && call.status_code && call.status_code !== 'N/A' && (
                      <Grid item xs={6} sm={3} md={2}>
                        <Typography variant="caption" color="text.secondary">HTTP Status</Typography>
                        <Typography variant="body2">{call.status_code}</Typography>
                      </Grid>
                    )}
                  </Grid>

                  <Divider />

                  {/* Input Arguments */}
                  <Box>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                      <InputIcon color="primary" fontSize="small" />
                      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                        Input Arguments
                      </Typography>
                    </Box>
                    <Box
                      sx={{
                        backgroundColor: 'rgba(33, 150, 243, 0.08)',
                        p: 2,
                        borderRadius: 1,
                        borderLeft: '4px solid',
                        borderLeftColor: 'primary.main'
                      }}
                    >
                      <FormattedArguments args={call.input} />
                    </Box>
                    <CollapsibleJson data={call.input} label="Raw Input JSON" />
                  </Box>

                  {/* Tool Result (if available) */}
                  {call.tool_result && (
                    <Box>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                        <OutputIcon color="success" fontSize="small" />
                        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                          Tool Result
                        </Typography>
                        {call.tool_result_length && call.tool_result_length > 0 && (
                          <Chip
                            label={`${call.tool_result_length.toLocaleString()} chars`}
                            size="small"
                            variant="outlined"
                          />
                        )}
                      </Box>
                      <FormattedToolResult
                        result={call.tool_result}
                        resultLength={call.tool_result_length || 0}
                      />
                    </Box>
                  )}

                  {/* Raw Output (function_call metadata) - collapsible */}
                  {call.output && (
                    <Box>
                      <CollapsibleJson data={call.output} label="Raw Function Call Metadata" />
                    </Box>
                  )}

                  {/* Observation ID */}
                  <Box sx={{ mt: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      Observation ID: <code style={{ fontSize: '0.75rem' }}>{call.id}</code>
                    </Typography>
                  </Box>
                </Box>
              </AccordionDetails>
            </Accordion>
          ))}
        </Box>
      )}
    </Box>
  );
}
