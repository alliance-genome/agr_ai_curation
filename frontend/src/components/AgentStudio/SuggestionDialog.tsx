/**
 * SuggestionDialog Component
 *
 * Dialog for manually submitting prompt improvement suggestions.
 */

import { useState } from 'react'
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Box,
  Typography,
  CircularProgress,
  Alert,
} from '@mui/material'
import LightbulbIcon from '@mui/icons-material/Lightbulb'

import { submitSuggestion } from '@/services/agentStudioService'
import type { ChatContext, PromptInfo, SuggestionType } from '@/types/promptExplorer'

interface SuggestionDialogProps {
  open: boolean
  onClose: () => void
  onSuccess: (suggestionId: string) => void
  onError: (error: string) => void
  context: ChatContext
  selectedAgent?: PromptInfo
}

const SUGGESTION_TYPES: { value: SuggestionType; label: string; description: string }[] = [
  {
    value: 'improvement',
    label: 'Improvement',
    description: 'General enhancement to make the prompt better',
  },
  {
    value: 'bug',
    label: 'Bug',
    description: 'The prompt produces incorrect or unexpected behavior',
  },
  {
    value: 'clarification',
    label: 'Clarification',
    description: 'The prompt is ambiguous or unclear',
  },
  {
    value: 'mod_specific',
    label: 'MOD-Specific',
    description: 'Change needed for a specific Model Organism Database',
  },
  {
    value: 'missing_case',
    label: 'Missing Case',
    description: 'The prompt doesn\'t handle a particular scenario',
  },
  {
    value: 'general',
    label: 'General Feedback',
    description: 'Feedback based on trace/conversation (not tied to a specific prompt)',
  },
]

function SuggestionDialog({
  open,
  onClose,
  onSuccess,
  onError,
  context,
  selectedAgent,
}: SuggestionDialogProps) {
  const [suggestionType, setSuggestionType] = useState<SuggestionType>('improvement')
  const [summary, setSummary] = useState('')
  const [reasoning, setReasoning] = useState('')
  const [proposedChange, setProposedChange] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    // Require either an agent or a trace_id for context
    if (!selectedAgent && !context.trace_id) {
      setError('No agent or trace context available')
      return
    }

    if (!summary.trim()) {
      setError('Please provide a summary')
      return
    }

    if (!reasoning.trim()) {
      setError('Please provide detailed reasoning')
      return
    }

    setIsSubmitting(true)
    setError(null)

    try {
      const response = await submitSuggestion({
        agent_id: selectedAgent?.agent_id,  // Optional - may be undefined for trace-based feedback
        suggestion_type: suggestionType,
        summary: summary.trim(),
        detailed_reasoning: reasoning.trim(),
        proposed_change: proposedChange.trim() || undefined,
        mod_id: context.selected_mod_id,
        trace_id: context.trace_id,
      })

      // Reset form
      setSuggestionType('improvement')
      setSummary('')
      setReasoning('')
      setProposedChange('')

      onSuccess(response.suggestion_id)
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error'
      setError(errorMessage)
      onError(errorMessage)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleClose = () => {
    if (!isSubmitting) {
      setError(null)
      onClose()
    }
  }

  const selectedTypeInfo = SUGGESTION_TYPES.find((t) => t.value === suggestionType)

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <LightbulbIcon color="primary" />
        Submit Prompt Suggestion
      </DialogTitle>

      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, mt: 1 }}>
          {/* Context info - show agent or trace info */}
          {(selectedAgent || context.trace_id) && (
            <Alert severity="info" sx={{ py: 0.5 }}>
              <Typography variant="body2">
                {selectedAgent ? (
                  <>
                    Submitting suggestion for: <strong>{selectedAgent.agent_name}</strong>
                    {context.selected_mod_id && ` (${context.selected_mod_id})`}
                  </>
                ) : (
                  <>Submitting general feedback based on conversation</>
                )}
                {context.trace_id && (
                  <>
                    <br />
                    <Typography component="span" variant="caption" color="text.secondary">
                      Related trace: {context.trace_id}
                    </Typography>
                  </>
                )}
              </Typography>
            </Alert>
          )}

          {/* Error display */}
          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          {/* Suggestion type */}
          <FormControl fullWidth size="small">
            <InputLabel>Suggestion Type</InputLabel>
            <Select
              value={suggestionType}
              label="Suggestion Type"
              onChange={(e) => setSuggestionType(e.target.value as SuggestionType)}
              disabled={isSubmitting}
            >
              {SUGGESTION_TYPES.map((type) => (
                <MenuItem key={type.value} value={type.value}>
                  {type.label}
                </MenuItem>
              ))}
            </Select>
            {selectedTypeInfo && (
              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, ml: 1.5 }}>
                {selectedTypeInfo.description}
              </Typography>
            )}
          </FormControl>

          {/* Summary */}
          <TextField
            label="Summary"
            placeholder="Brief 1-2 sentence summary of your suggestion"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            fullWidth
            size="small"
            disabled={isSubmitting}
            required
            inputProps={{ maxLength: 200 }}
            helperText={`${summary.length}/200 characters`}
          />

          {/* Detailed reasoning */}
          <TextField
            label="Detailed Reasoning"
            placeholder="Explain why this change is needed and what problem it solves"
            value={reasoning}
            onChange={(e) => setReasoning(e.target.value)}
            fullWidth
            multiline
            rows={4}
            disabled={isSubmitting}
            required
          />

          {/* Proposed change (optional) */}
          <TextField
            label="Proposed Change (Optional)"
            placeholder="If you have specific wording or changes in mind, describe them here"
            value={proposedChange}
            onChange={(e) => setProposedChange(e.target.value)}
            fullWidth
            multiline
            rows={3}
            disabled={isSubmitting}
          />
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={handleClose} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={isSubmitting || !summary.trim() || !reasoning.trim()}
          startIcon={isSubmitting ? <CircularProgress size={16} color="inherit" /> : <LightbulbIcon />}
        >
          {isSubmitting ? 'Submitting...' : 'Submit Suggestion'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default SuggestionDialog
