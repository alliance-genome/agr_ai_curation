/**
 * NodePanel
 *
 * The one-screen panel for the selected flow node. It holds only what the
 * step owns: instructions for this step, the optional automatic checks as
 * switches, output options for formatters, the steering prompt for custom
 * validator steps, and the task instructions for the input node. Reference
 * about the agent itself (guide, envelope, prompts) lives in the Agent
 * Browser, one link away in the "About this agent" row.
 *
 * Edits are local until Apply. Cancel reverts. Leaving the step with unapplied
 * edits asks Apply, Discard, or Keep editing through the promise-based leave
 * guard the parent calls before it changes the selection.
 */

import { useCallback, useImperativeHandle, useMemo, useState } from 'react'
import type { ReactNode, Ref } from 'react'
import {
  Alert,
  Box,
  Button,
  Collapse,
  FormControlLabel,
  Link,
  Radio,
  RadioGroup,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import LinkIcon from '@mui/icons-material/Link'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'

import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import { useAgentIcon } from '@/hooks/useAgentIcon'
import { MONO_FONT_FAMILY, SectionHeading } from '../../agentGuidePrimitives'
import {
  isExtractionAgentFromMetadata,
  isFileOutputFormatterAgentFromMetadata,
  isOutputFormatterAgentFromMetadata,
  isValidationAgentFromMetadata,
} from '../agentMetadataUtils'
import type { AgentBrowserRequest, AgentNode, AgentNodeData, OutputBindingView } from '../types'
import AutomaticChecks from './AutomaticChecks'
import NodePanelHeader from './NodePanelHeader'
import type { NodePanelStatus } from './NodePanelHeader'
import UnsavedEditsDialog from './UnsavedEditsDialog'
import { buildAutomaticChecksView } from './automaticChecks'
import type { NodePanelMode } from './nodePanelLayout'
import {
  BUILT_IN_TEMPLATE_VARIABLES,
  outputFileExtension,
  useNodeDraft,
} from './useNodeDraft'
import type { OutputFilenameMode } from './useNodeDraft'

export interface NodePanelLeaveGuard {
  /** Resolves true when the parent may change the selection. */
  requestLeave: () => Promise<boolean>
}

/** How a custom validator step is attached, derived from the flow's edges. */
export interface ValidatorAttachmentView {
  sourceLabel: string
  sourceStep: number
  /** Display name of the automatic check this step replaces, null when it adds a check. */
  replacesLabel: string | null
}

export interface NodePanelProps {
  node: AgentNode
  stepNumber: number
  stepCount: number
  /** Step numbers by node id, for sentences that name other steps. */
  stepNumbersById: Record<string, number>
  outputBinding?: OutputBindingView
  validatorAttachment?: ValidatorAttachmentView | null
  mode: NodePanelMode
  onApply: (nodeId: string, data: Partial<AgentNodeData>) => void
  onDelete: (nodeId: string) => void
  onHide: () => void
  onTaskInstructionsAuthored?: () => void
  onOpenAgent?: (request: AgentBrowserRequest) => void
  leaveGuardRef?: Ref<NodePanelLeaveGuard>
}

type StepKind = 'input' | 'extraction' | 'validation' | 'output' | 'agent'

const KIND_LABEL: Record<StepKind, string> = {
  input: 'Task input',
  extraction: 'Extraction step',
  validation: 'Validation step',
  output: 'Output step',
  agent: 'Agent step',
}

interface PendingLeave {
  proceed: () => void
  cancel: () => void
}

function Section({ heading, action, help, children }: { heading: string; action?: ReactNode; help?: string; children: ReactNode }) {
  return (
    <Box component="section" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
      <SectionHeading action={action}>{heading}</SectionHeading>
      {children}
      {help && <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{help}</Typography>}
    </Box>
  )
}

function OptionalMark() {
  return <Box component="span" sx={{ fontSize: 12, color: 'primary.main', fontWeight: 500, textTransform: 'none', letterSpacing: 0 }}>Optional</Box>
}

const textFieldSx = { '& .MuiInputBase-root': { fontSize: 13 } } as const

function NodePanel({
  node,
  stepNumber,
  stepCount,
  stepNumbersById,
  outputBinding,
  validatorAttachment,
  mode,
  onApply,
  onDelete,
  onHide,
  onTaskInstructionsAuthored,
  onOpenAgent,
  leaveGuardRef,
}: NodePanelProps) {
  const { agents: agentMetadata } = useAgentMetadata()
  const icon = useAgentIcon(node.data.agent_id)
  const agentId = node.data.agent_id
  const isTaskInput = node.type === 'task_input' || agentId === 'task_input'
  const kind: StepKind = isTaskInput
    ? 'input'
    : isValidationAgentFromMetadata(agentId, agentMetadata)
      ? 'validation'
      : isOutputFormatterAgentFromMetadata(agentId, agentMetadata)
        ? 'output'
        : isExtractionAgentFromMetadata(agentId, agentMetadata)
          ? 'extraction'
          : 'agent'
  const supportsFileOutputNaming = isFileOutputFormatterAgentFromMetadata(agentId, agentMetadata)
  const envelopeMetadata = agentMetadata[agentId]?.domain_envelope ?? null

  const draft = useNodeDraft({ node, agentMetadata, isTaskInput, supportsFileOutputNaming })
  const [pendingLeave, setPendingLeave] = useState<PendingLeave | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const checksView = useMemo(
    () => buildAutomaticChecksView(draft.values.attachments, node.data.validation_groups ?? [], envelopeMetadata),
    [draft.values.attachments, node.data.validation_groups, envelopeMetadata]
  )

  const applyDraft = useCallback((): boolean => {
    const payload = draft.buildPayload()
    if (!payload) return false
    onApply(node.id, payload)
    if (isTaskInput) onTaskInstructionsAuthored?.()
    return true
  }, [draft, isTaskInput, node.id, onApply, onTaskInstructionsAuthored])

  const requestLeave = useCallback((): Promise<boolean> => {
    if (!draft.dirty) return Promise.resolve(true)
    return new Promise<boolean>((resolve) => {
      setPendingLeave({
        proceed: () => resolve(true),
        cancel: () => resolve(false),
      })
    })
  }, [draft.dirty])

  useImperativeHandle(leaveGuardRef, () => ({ requestLeave }), [requestLeave])

  // Hiding the panel unmounts it, draft included, so it goes through the same guard.
  const guardedHide = useCallback(() => {
    void requestLeave().then((leave) => {
      if (leave) onHide()
    })
  }, [onHide, requestLeave])

  const guardedOpenAgent = useCallback((request: AgentBrowserRequest) => {
    if (!onOpenAgent) return
    void requestLeave().then((leave) => {
      if (leave) onOpenAgent(request)
    })
  }, [onOpenAgent, requestLeave])

  const status: NodePanelStatus = node.data.hasError ? 'error' : draft.dirty ? 'dirty' : 'clean'
  const stepLabel = `Step ${stepNumber} of ${stepCount}`
  const stepDetail = isTaskInput
    ? 'task input'
    : `${agentId}${node.data.prompt_version ? ` v${node.data.prompt_version}` : ''}`

  const fileExtension = outputFileExtension(agentId)
  const filenamePreviewPrefix = draft.values.outputFilenameMode === 'source_pdf'
    ? '<PDF-name>'
    : draft.values.outputFilenameMode === 'custom'
      ? draft.values.outputFilenameTemplate.trim() || '<custom-prefix>'
      : '<formatter-name>'
  const filenamePreview = `${filenamePreviewPrefix}_<node>_<hash>_<trace-id>.${fileExtension}`
  const customFilenameMissing = supportsFileOutputNaming
    && draft.values.outputFilenameMode === 'custom'
    && !draft.values.outputFilenameTemplate.trim()

  const aboutSubtitle = envelopeMetadata
    ? `${node.data.agent_display_name} · guide, what it produces, automatic checks, prompts`
    : `${node.data.agent_display_name} · guide and prompts`

  return (
    <Box
      component="aside"
      aria-label={`${node.data.agent_display_name} step settings`}
      sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, backgroundColor: 'background.paper' }}
    >
      <NodePanelHeader
        icon={icon}
        name={node.data.agent_display_name}
        stepLabel={stepLabel}
        stepDetail={stepDetail}
        kindLabel={KIND_LABEL[kind]}
        status={status}
        errorMessage={node.data.errorMessage}
        applyDisabled={!draft.dirty || Boolean(draft.blockingError)}
        mode={mode}
        onApply={() => { applyDraft() }}
        onCancel={draft.reset}
        onDelete={() => onDelete(node.id)}
        onHide={guardedHide}
      />

      <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto', p: 1.75, display: 'flex', flexDirection: 'column', gap: 2 }}>
        {kind === 'input' && (
          <Section
            heading="Task instructions"
            help="Passed to the first agent in the flow. Describe the curation task in plain words."
          >
            <TextField
              fullWidth
              size="small"
              multiline
              rows={6}
              required
              placeholder="e.g., Extract every disease assertion in this paper, confirm each disease term against the ontology, and return a TSV with the supporting quote for each row."
              value={draft.values.taskInstructions}
              onChange={(event) => draft.set('taskInstructions', event.target.value)}
              error={!draft.values.taskInstructions.trim()}
              helperText={!draft.values.taskInstructions.trim() ? 'Task instructions are required.' : undefined}
              inputProps={{ 'aria-label': 'Task instructions' }}
              sx={textFieldSx}
            />
          </Section>
        )}

        {kind === 'validation' && (
          <Alert
            severity="info"
            icon={<LinkIcon fontSize="inherit" />}
            sx={{ py: 0.5, '& .MuiAlert-message': { fontSize: 12.5 } }}
          >
            {validatorAttachment
              ? validatorAttachment.replacesLabel
                ? <>Attaches to <strong>{validatorAttachment.sourceLabel}</strong> (step {validatorAttachment.sourceStep}) and replaces its {validatorAttachment.replacesLabel} check for this flow.</>
                : <>Attaches to <strong>{validatorAttachment.sourceLabel}</strong> (step {validatorAttachment.sourceStep}) and adds a check beside its automatic checks.</>
              : 'Not attached to an extraction step yet. Connect this validator to the validation port of an extraction step.'}
          </Alert>
        )}

        {kind === 'validation' && (
          <Section
            heading="Steering prompt"
            action={<OptionalMark />}
            help="Added to this validator's prompt for this step only."
          >
            <TextField
              fullWidth
              size="small"
              multiline
              rows={4}
              placeholder="e.g., Accept a term only when its primary label appears verbatim in the evidence quote."
              value={draft.values.customInstructions}
              onChange={(event) => draft.set('customInstructions', event.target.value)}
              inputProps={{ 'aria-label': 'Steering prompt' }}
              sx={textFieldSx}
            />
          </Section>
        )}

        {(kind === 'extraction' || kind === 'agent') && (
          <Section
            heading="Instructions for this step"
            action={<OptionalMark />}
            help="Added to the agent's prompt for this flow only."
          >
            <TextField
              fullWidth
              size="small"
              multiline
              rows={3}
              placeholder="e.g., Only extract records named in the results section."
              value={draft.values.customInstructions}
              onChange={(event) => draft.set('customInstructions', event.target.value)}
              inputProps={{ 'aria-label': 'Instructions for this step' }}
              sx={textFieldSx}
            />
          </Section>
        )}

        {(kind === 'extraction' || kind === 'agent') && (envelopeMetadata || draft.values.attachments.length > 0) && (
          <AutomaticChecks
            view={checksView}
            envelopeAgentId={agentId}
            agentMetadata={agentMetadata}
            onToggle={draft.setAttachmentsEnabled}
            onOpenAgent={onOpenAgent ? guardedOpenAgent : undefined}
          />
        )}

        {kind === 'output' && (
          outputBinding?.status === 'bound' ? (
            <Alert
              severity="info"
              icon={<DescriptionOutlinedIcon fontSize="inherit" />}
              sx={{ py: 0.5, '& .MuiAlert-message': { fontSize: 12.5 } }}
            >
              {outputBinding.sources.length === 1 ? (
                <>
                  Formats the results of <strong>{outputBinding.sources[0].sourceLabel}</strong>
                  {stepNumbersById[outputBinding.sources[0].sourceNodeId] ? ` (step ${stepNumbersById[outputBinding.sources[0].sourceNodeId]})` : ''}.
                </>
              ) : (
                <>
                  Formats the results of <strong>{outputBinding.sources.length} steps</strong> as one grouped input:{' '}
                  {outputBinding.sources.map((source) => source.sourceLabel).join(', ')}.
                </>
              )}
            </Alert>
          ) : (
            <Alert severity="error" sx={{ py: 0.5, '& .MuiAlert-message': { fontSize: 12.5 } }}>
              {outputBinding?.status === 'duplicate'
                ? 'The same source step is attached to this formatter more than once. Remove the duplicate connection.'
                : outputBinding?.status === 'incompatible'
                  ? 'This formatter is connected to an incompatible step. Connect it only to extraction results or typed validation results.'
                  : 'This formatter is not connected. Connect at least one extraction result or typed validation result.'}
            </Alert>
          )
        )}

        {kind === 'output' && (
          <Section heading="Output">
            <FormControlLabel
              sx={{ m: 0, gap: 0.75, '& .MuiFormControlLabel-label': { fontSize: 12.5 } }}
              control={(
                <Switch
                  size="small"
                  checked={draft.values.includeEvidence}
                  onChange={(event) => draft.set('includeEvidence', event.target.checked)}
                  inputProps={{ role: 'switch' }}
                />
              )}
              label="Include the supporting evidence in the output"
            />
          </Section>
        )}

        {kind === 'output' && supportsFileOutputNaming && (
          <Section heading="File name">
            <RadioGroup
              aria-label="File name"
              value={draft.values.outputFilenameMode}
              onChange={(event) => draft.set('outputFilenameMode', event.target.value as OutputFilenameMode)}
              sx={{ gap: 0.25, '& .MuiFormControlLabel-label': { fontSize: 12.5 } }}
            >
              <FormControlLabel value="source_pdf" control={<Radio size="small" />} label="Use the paper's file name (recommended)" />
              <FormControlLabel value="custom" control={<Radio size="small" />} label="Custom prefix" />
              <FormControlLabel value="formatter_default" control={<Radio size="small" />} label="Let the formatter decide" />
            </RadioGroup>

            <Collapse in={draft.values.outputFilenameMode === 'custom'} unmountOnExit>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, pt: 0.5 }}>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                  {BUILT_IN_TEMPLATE_VARIABLES.map((variable) => (
                    <Button
                      key={variable}
                      size="small"
                      variant="outlined"
                      onClick={() => draft.set('outputFilenameTemplate', `${draft.values.outputFilenameTemplate}{{${variable}}}`)}
                      sx={{ textTransform: 'none', fontFamily: MONO_FONT_FAMILY, fontSize: 11, px: 0.75, py: 0, minWidth: 0 }}
                    >
                      {`{{${variable}}}`}
                    </Button>
                  ))}
                </Box>
                <TextField
                  fullWidth
                  required
                  size="small"
                  label="Custom prefix"
                  placeholder="results_{{input_filename_stem}}"
                  value={draft.values.outputFilenameTemplate}
                  onChange={(event) => draft.set('outputFilenameTemplate', event.target.value)}
                  error={customFilenameMissing}
                  helperText={customFilenameMissing
                    ? 'Enter a custom prefix before applying.'
                    : 'The file extension is added automatically.'}
                  sx={textFieldSx}
                />
              </Box>
            </Collapse>

            <Typography sx={{ fontSize: 11.5, fontFamily: MONO_FONT_FAMILY, color: 'text.secondary', overflowWrap: 'anywhere' }}>
              {filenamePreview}
            </Typography>
          </Section>
        )}

        <Box>
          <Button
            size="small"
            onClick={() => setAdvancedOpen((current) => !current)}
            aria-expanded={advancedOpen}
            startIcon={advancedOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            sx={{ textTransform: 'none', fontSize: 12.5, px: 0.5, color: 'text.secondary' }}
          >
            Output variable
          </Button>
          <Collapse in={advancedOpen} unmountOnExit>
            <Box sx={{ pt: 0.75 }}>
              <TextField
                fullWidth
                size="small"
                label="Output variable name"
                value={draft.values.outputKey}
                onChange={(event) => draft.set('outputKey', event.target.value.replace(/[^a-zA-Z0-9_]/g, '_'))}
                helperText="Names this step's saved result for later steps and exports."
                inputProps={{ style: { fontFamily: MONO_FONT_FAMILY, fontSize: 13 } }}
              />
            </Box>
          </Collapse>
        </Box>

        {kind !== 'input' && (
          <Box
            sx={{
              mt: 'auto',
              display: 'flex',
              alignItems: 'center',
              gap: 1.25,
              p: 1.25,
              border: 1,
              borderColor: 'divider',
              borderRadius: 2,
              backgroundColor: 'background.default',
            }}
          >
            <Box
              aria-hidden="true"
              sx={{
                width: 28,
                height: 28,
                borderRadius: 1.5,
                flex: 'none',
                display: 'grid',
                placeItems: 'center',
                fontSize: 14,
                backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.12),
              }}
            >
              {icon}
            </Box>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography sx={{ fontSize: 12.5, fontWeight: 600 }}>About this agent</Typography>
              <Typography sx={{ fontSize: 12, color: 'text.secondary', overflowWrap: 'anywhere' }}>{aboutSubtitle}</Typography>
            </Box>
            {onOpenAgent && (
              <Box sx={{ display: 'flex', gap: 1.25, flex: 'none' }}>
                <Link component="button" type="button" underline="hover" sx={{ fontSize: 12.5, fontWeight: 500 }} onClick={() => guardedOpenAgent({ agentId, tab: 'guide' })}>
                  Guide
                </Link>
                {envelopeMetadata && (
                  <Link component="button" type="button" underline="hover" sx={{ fontSize: 12.5, fontWeight: 500 }} onClick={() => guardedOpenAgent({ agentId, tab: 'envelope' })}>
                    Envelope
                  </Link>
                )}
                <Link component="button" type="button" underline="hover" sx={{ fontSize: 12.5, fontWeight: 500 }} onClick={() => guardedOpenAgent({ agentId, tab: 'prompts' })}>
                  Prompts
                </Link>
              </Box>
            )}
          </Box>
        )}
      </Box>

      <UnsavedEditsDialog
        open={pendingLeave !== null}
        stepNumber={stepNumber}
        changeSummary={draft.changeSummary}
        blockingError={draft.blockingError}
        onApply={() => {
          if (!pendingLeave) return
          if (!applyDraft()) return
          setPendingLeave(null)
          pendingLeave.proceed()
        }}
        onDiscard={() => {
          if (!pendingLeave) return
          draft.reset()
          setPendingLeave(null)
          pendingLeave.proceed()
        }}
        onKeepEditing={() => {
          if (!pendingLeave) return
          setPendingLeave(null)
          pendingLeave.cancel()
        }}
      />
    </Box>
  )
}

export default NodePanel
