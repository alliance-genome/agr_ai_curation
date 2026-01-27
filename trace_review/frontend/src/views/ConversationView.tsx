import { Box, Typography, Paper, IconButton, Tooltip, Chip } from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import PersonIcon from '@mui/icons-material/Person';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import CheckIcon from '@mui/icons-material/Check';
import { useState } from 'react';
import { ConversationData } from '../types';

interface ConversationViewProps {
  data: ConversationData;
}

// Helper to safely convert response to string (handles OpenAI message format)
function extractText(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (value === null || value === undefined) {
    return 'N/A';
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    // Handle OpenAI response format: {annotations, text, type, logprobs}
    if ('text' in obj && typeof obj.text === 'string') {
      return obj.text;
    }
    // Handle other common formats
    if ('content' in obj && typeof obj.content === 'string') {
      return obj.content;
    }
    if ('response' in obj && typeof obj.response === 'string') {
      return obj.response;
    }
    // Last resort: stringify
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

export function ConversationView({ data }: ConversationViewProps) {
  const userInput = extractText(data.user_input);
  const assistantResponse = extractText(data.assistant_response);
  const [copiedUser, setCopiedUser] = useState(false);
  const [copiedAssistant, setCopiedAssistant] = useState(false);

  const handleCopy = async (text: string, type: 'user' | 'assistant') => {
    await navigator.clipboard.writeText(text);
    if (type === 'user') {
      setCopiedUser(true);
      setTimeout(() => setCopiedUser(false), 2000);
    } else {
      setCopiedAssistant(true);
      setTimeout(() => setCopiedAssistant(false), 2000);
    }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        Conversation
      </Typography>

      {/* User Input */}
      <Paper
        sx={{
          p: 3,
          mb: 3,
          borderLeft: '4px solid #2196f3',
          backgroundColor: 'rgba(33, 150, 243, 0.05)'
        }}
      >
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <PersonIcon color="primary" />
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              User Query
            </Typography>
          </Box>
          <Tooltip title={copiedUser ? "Copied!" : "Copy user input"}>
            <IconButton
              size="small"
              onClick={() => handleCopy(userInput, 'user')}
              color={copiedUser ? "success" : "default"}
            >
              {copiedUser ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
        </Box>
        <Typography
          variant="body1"
          component="div"
          sx={{
            fontStyle: userInput === 'N/A' ? 'italic' : 'normal',
            color: userInput === 'N/A' ? 'text.disabled' : 'text.primary',
            fontSize: '1.1rem',
            lineHeight: 1.6
          }}
        >
          {userInput}
        </Typography>
      </Paper>

      {/* Assistant Response */}
      <Paper
        sx={{
          p: 3,
          borderLeft: '4px solid #4caf50',
          backgroundColor: 'rgba(76, 175, 80, 0.05)'
        }}
      >
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <SmartToyIcon color="success" />
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              Assistant Response
            </Typography>
            {assistantResponse && assistantResponse !== 'N/A' && (
              <Chip
                label={`${assistantResponse.length.toLocaleString()} chars`}
                size="small"
                variant="outlined"
                sx={{ ml: 1 }}
              />
            )}
          </Box>
          <Tooltip title={copiedAssistant ? "Copied!" : "Copy assistant response"}>
            <IconButton
              size="small"
              onClick={() => handleCopy(assistantResponse, 'assistant')}
              color={copiedAssistant ? "success" : "default"}
            >
              {copiedAssistant ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
        </Box>
        <Typography
          variant="body1"
          component="div"
          sx={{
            whiteSpace: 'pre-wrap',
            fontStyle: assistantResponse === 'N/A' ? 'italic' : 'normal',
            color: assistantResponse === 'N/A' ? 'text.disabled' : 'text.primary',
            lineHeight: 1.8,
            '& p': { marginBottom: '1em' }
          }}
        >
          {assistantResponse}
        </Typography>
      </Paper>

      {/* Metadata */}
      <Box sx={{ mt: 3 }}>
        <Typography variant="subtitle2" color="text.secondary" gutterBottom>
          Trace Metadata
        </Typography>
        <Paper sx={{ p: 2 }}>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
            <Box>
              <Typography variant="caption" color="text.secondary">Trace Name</Typography>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                {data.trace_name || 'N/A'}
              </Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary">Session ID</Typography>
              <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                {data.session_id || 'N/A'}
              </Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary">Timestamp</Typography>
              <Typography variant="body2">
                {data.timestamp ? new Date(data.timestamp).toLocaleString() : 'N/A'}
              </Typography>
            </Box>
          </Box>
        </Paper>
      </Box>
    </Box>
  );
}
