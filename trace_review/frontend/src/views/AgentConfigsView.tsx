import { useState, useMemo } from 'react';
import {
  Box,
  Typography,
  Paper,
  Chip,
  Card,
  CardContent,
  Grid,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Divider,
  Tooltip,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import BuildIcon from '@mui/icons-material/Build';
import SettingsIcon from '@mui/icons-material/Settings';
import DescriptionIcon from '@mui/icons-material/Description';
import CodeIcon from '@mui/icons-material/Code';
import FormatListBulletedIcon from '@mui/icons-material/FormatListBulleted';
import HistoryIcon from '@mui/icons-material/History';
import { AgentConfigsData, AgentConfigEntry, PromptVersionSummary } from '../types';

// Helper to get prompt version from agent config
function getPromptVersion(agent: AgentConfigEntry): number | null {
  const { model_settings } = agent;
  if (model_settings.prompt_version !== undefined) {
    return model_settings.prompt_version;
  }
  // Formatter agent uses base_prompt_version
  if (model_settings.base_prompt_version !== undefined) {
    return model_settings.base_prompt_version;
  }
  return null;
}

// Compute prompt version summary from agents
function computePromptVersionSummary(agents: AgentConfigEntry[]): PromptVersionSummary[] {
  const versionMap = new Map<number, string[]>();

  for (const agent of agents) {
    const version = getPromptVersion(agent);
    if (version !== null) {
      const existing = versionMap.get(version) || [];
      existing.push(agent.agent_name);
      versionMap.set(version, existing);
    }
  }

  return Array.from(versionMap.entries())
    .map(([version, agentNames]) => ({
      version,
      agent_count: agentNames.length,
      agents: agentNames,
    }))
    .sort((a, b) => b.version - a.version); // Newest first
}

interface AgentConfigsViewProps {
  data: AgentConfigsData;
}

function AgentCard({ agent, defaultExpanded }: { agent: AgentConfigEntry; defaultExpanded?: boolean }) {
  const [instructionsExpanded, setInstructionsExpanded] = useState(false);
  const [metadataExpanded, setMetadataExpanded] = useState(false);
  const promptVersion = getPromptVersion(agent);

  return (
    <Accordion defaultExpanded={defaultExpanded}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, width: '100%' }}>
          <SmartToyIcon color="primary" />
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            {agent.agent_name}
          </Typography>
          {promptVersion !== null && (
            <Tooltip title="Prompt template version from database">
              <Chip
                icon={<HistoryIcon />}
                label={`v${promptVersion}`}
                size="small"
                color="info"
                variant="filled"
                sx={{ fontWeight: 'bold' }}
              />
            </Tooltip>
          )}
          <Chip
            label={agent.model}
            size="small"
            color="secondary"
            variant="outlined"
          />
          <Chip
            label={`${agent.tools.length} tools`}
            size="small"
            variant="outlined"
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Grid container spacing={2}>
          {/* Model Settings */}
          <Grid item xs={12} md={6}>
            <Paper sx={{ p: 2, backgroundColor: 'rgba(0, 0, 0, 0.02)' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                <SettingsIcon fontSize="small" color="primary" />
                <Typography variant="subtitle2" fontWeight="bold">Model Settings</Typography>
              </Box>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Typography variant="body2" color="text.secondary">Model</Typography>
                  <Typography variant="body2" fontFamily="monospace">{agent.model}</Typography>
                </Box>
                {agent.model_settings.temperature !== undefined && agent.model_settings.temperature !== null && (
                  <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Typography variant="body2" color="text.secondary">Temperature</Typography>
                    <Typography variant="body2" fontFamily="monospace">{agent.model_settings.temperature}</Typography>
                  </Box>
                )}
                {agent.model_settings.reasoning && (
                  <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Typography variant="body2" color="text.secondary">Reasoning</Typography>
                    <Typography variant="body2" fontFamily="monospace">{agent.model_settings.reasoning}</Typography>
                  </Box>
                )}
                {agent.model_settings.tool_choice && (
                  <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Typography variant="body2" color="text.secondary">Tool Choice</Typography>
                    <Typography variant="body2" fontFamily="monospace">{agent.model_settings.tool_choice}</Typography>
                  </Box>
                )}
                {/* Prompt Version */}
                {promptVersion !== null && (
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mt: 1, pt: 1, borderTop: '1px dashed rgba(0,0,0,0.1)' }}>
                    <Typography variant="body2" color="text.secondary">Prompt Version</Typography>
                    <Chip
                      icon={<HistoryIcon />}
                      label={`v${promptVersion}`}
                      size="small"
                      color="info"
                      variant="filled"
                    />
                  </Box>
                )}
                {/* Formatter agent shows both versions */}
                {agent.model_settings.format_prompt_version !== undefined && (
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Typography variant="body2" color="text.secondary">Format Prompt Version</Typography>
                    <Chip
                      label={`v${agent.model_settings.format_prompt_version}`}
                      size="small"
                      color="info"
                      variant="outlined"
                    />
                  </Box>
                )}
              </Box>
            </Paper>
          </Grid>

          {/* Tools Available */}
          <Grid item xs={12} md={6}>
            <Paper sx={{ p: 2, backgroundColor: 'rgba(0, 0, 0, 0.02)' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                <BuildIcon fontSize="small" color="primary" />
                <Typography variant="subtitle2" fontWeight="bold">Available Tools</Typography>
              </Box>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {agent.tools.length > 0 ? (
                  agent.tools.map((tool) => (
                    <Chip
                      key={tool}
                      label={tool}
                      size="small"
                      variant="outlined"
                      color="primary"
                    />
                  ))
                ) : (
                  <Typography variant="body2" color="text.secondary">No tools</Typography>
                )}
              </Box>
            </Paper>
          </Grid>

          {/* Metadata (if present) */}
          {agent.metadata && Object.keys(agent.metadata).length > 0 && (
            <Grid item xs={12}>
              <Accordion
                expanded={metadataExpanded}
                onChange={() => setMetadataExpanded(!metadataExpanded)}
                sx={{ backgroundColor: 'rgba(0, 0, 0, 0.02)' }}
              >
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <DescriptionIcon fontSize="small" color="primary" />
                    <Typography variant="subtitle2" fontWeight="bold">Metadata</Typography>
                    <Chip
                      label={`${Object.keys(agent.metadata).length} fields`}
                      size="small"
                      variant="outlined"
                    />
                  </Box>
                </AccordionSummary>
                <AccordionDetails>
                  <Paper
                    sx={{
                      p: 2,
                      backgroundColor: '#1e1e1e',
                      maxHeight: 300,
                      overflow: 'auto',
                      borderRadius: 1,
                    }}
                  >
                    <Typography
                      component="pre"
                      sx={{
                        fontFamily: 'monospace',
                        fontSize: '0.8rem',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        color: '#d4d4d4',
                        margin: 0,
                      }}
                    >
                      {JSON.stringify(agent.metadata, null, 2)}
                    </Typography>
                  </Paper>
                </AccordionDetails>
              </Accordion>
            </Grid>
          )}

          {/* Instruction Stats */}
          <Grid item xs={12}>
            <Paper sx={{ p: 2, backgroundColor: 'rgba(0, 0, 0, 0.02)' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                <DescriptionIcon fontSize="small" color="primary" />
                <Typography variant="subtitle2" fontWeight="bold">System Prompt Stats</Typography>
              </Box>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                <Chip
                  label={`${agent.instruction_stats.word_count.toLocaleString()} words`}
                  size="small"
                  variant="outlined"
                />
                <Chip
                  label={`${agent.instruction_stats.line_count.toLocaleString()} lines`}
                  size="small"
                  variant="outlined"
                />
                <Chip
                  label={`${agent.instruction_stats.char_count.toLocaleString()} chars`}
                  size="small"
                  variant="outlined"
                />
                {agent.instruction_stats.has_markdown_headings && (
                  <Chip label="Markdown headings" size="small" color="info" variant="outlined" />
                )}
                {agent.instruction_stats.has_code_blocks && (
                  <Chip icon={<CodeIcon />} label="Code blocks" size="small" color="info" variant="outlined" />
                )}
                {agent.instruction_stats.has_bullet_points && (
                  <Chip icon={<FormatListBulletedIcon />} label="Bullet points" size="small" color="info" variant="outlined" />
                )}
              </Box>
            </Paper>
          </Grid>

          {/* Full Instructions (Collapsible) */}
          <Grid item xs={12}>
            <Accordion
              expanded={instructionsExpanded}
              onChange={() => setInstructionsExpanded(!instructionsExpanded)}
              sx={{ backgroundColor: 'rgba(33, 150, 243, 0.05)' }}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <CodeIcon color="primary" />
                  <Typography variant="subtitle2" fontWeight="bold">
                    System Prompt / Instructions
                  </Typography>
                  <Chip
                    label={instructionsExpanded ? 'Click to collapse' : 'Click to expand'}
                    size="small"
                    variant="outlined"
                    color="primary"
                  />
                </Box>
              </AccordionSummary>
              <AccordionDetails>
                <Paper
                  sx={{
                    p: 2,
                    backgroundColor: '#1e1e1e',
                    maxHeight: 600,
                    overflow: 'auto',
                    borderRadius: 1,
                  }}
                >
                  <Typography
                    component="pre"
                    sx={{
                      fontFamily: 'monospace',
                      fontSize: '0.85rem',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                      color: '#d4d4d4',
                      margin: 0,
                      lineHeight: 1.5,
                    }}
                  >
                    {agent.instructions || '(No instructions available)'}
                  </Typography>
                </Paper>
              </AccordionDetails>
            </Accordion>
          </Grid>

          {/* Timestamp */}
          {agent.timestamp && (
            <Grid item xs={12}>
              <Typography variant="caption" color="text.secondary">
                Logged at: {new Date(agent.timestamp).toLocaleString()}
              </Typography>
            </Grid>
          )}
        </Grid>
      </AccordionDetails>
    </Accordion>
  );
}

export function AgentConfigsView({ data }: AgentConfigsViewProps) {
  // Compute prompt version summary
  const promptVersionSummary = useMemo(() => {
    return data?.prompt_versions || computePromptVersionSummary(data?.agents || []);
  }, [data]);

  const hasPromptVersions = promptVersionSummary.length > 0;
  const agentsWithVersions = promptVersionSummary.reduce((sum, v) => sum + v.agent_count, 0);

  if (!data || data.agent_count === 0) {
    return (
      <Alert severity="info">
        No agent configurations found in this trace. This view shows the system prompts and
        settings for each agent when they are logged via log_agent_config().
      </Alert>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <SmartToyIcon color="primary" fontSize="large" />
          <Typography variant="h5">Agent Configurations</Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          {hasPromptVersions && (
            <Tooltip title={`${agentsWithVersions} agents with prompt versions`}>
              <Chip
                icon={<HistoryIcon />}
                label={promptVersionSummary.length === 1 ? `v${promptVersionSummary[0].version}` : `${promptVersionSummary.length} versions`}
                color="info"
              />
            </Tooltip>
          )}
          <Chip
            label={`${data.agent_count} agent${data.agent_count !== 1 ? 's' : ''}`}
            color="primary"
          />
        </Box>
      </Box>

      {/* Summary Cards */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* Prompt Versions - show first if present */}
        {hasPromptVersions && (
          <Grid item xs={12} md={4}>
            <Card sx={{ backgroundColor: 'rgba(33, 150, 243, 0.05)', border: '1px solid rgba(33, 150, 243, 0.2)' }}>
              <CardContent>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                  <HistoryIcon color="info" />
                  <Typography variant="h6">Prompt Versions</Typography>
                </Box>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {promptVersionSummary.map((v) => (
                    <Box key={v.version} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Chip
                        label={`v${v.version}`}
                        color="info"
                        variant="filled"
                        size="small"
                        sx={{ fontWeight: 'bold', minWidth: 50 }}
                      />
                      <Typography variant="body2" color="text.secondary">
                        {v.agent_count} agent{v.agent_count !== 1 ? 's' : ''}
                      </Typography>
                      <Tooltip title={v.agents.join(', ')}>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            maxWidth: 150,
                            cursor: 'help'
                          }}
                        >
                          ({v.agents.slice(0, 2).join(', ')}{v.agents.length > 2 ? '...' : ''})
                        </Typography>
                      </Tooltip>
                    </Box>
                  ))}
                </Box>
              </CardContent>
            </Card>
          </Grid>
        )}

        {/* Models Used */}
        <Grid item xs={12} md={hasPromptVersions ? 4 : 6}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                <SettingsIcon color="primary" />
                <Typography variant="h6">Models Used</Typography>
              </Box>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                {data.models_used.map((model) => (
                  <Chip
                    key={model}
                    label={model}
                    color="secondary"
                    variant="outlined"
                  />
                ))}
              </Box>
            </CardContent>
          </Card>
        </Grid>

        {/* All Tools Available */}
        <Grid item xs={12} md={hasPromptVersions ? 4 : 6}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                <BuildIcon color="primary" />
                <Typography variant="h6">All Tools</Typography>
                <Chip label={data.tools_available.length} size="small" color="primary" />
              </Box>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {data.tools_available.map((tool) => (
                  <Chip
                    key={tool}
                    label={tool}
                    size="small"
                    variant="outlined"
                  />
                ))}
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Divider sx={{ mb: 3 }} />

      {/* Agent Cards */}
      <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <SmartToyIcon />
        Individual Agent Configurations
      </Typography>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {data.agents.map((agent, index) => (
          <AgentCard
            key={agent.observation_id || agent.event_name || index}
            agent={agent}
            defaultExpanded={index === 0}
          />
        ))}
      </Box>
    </Box>
  );
}
