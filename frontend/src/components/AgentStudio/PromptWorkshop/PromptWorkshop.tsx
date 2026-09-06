import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from 'react'
import { Alert, Box, Button, Typography } from '@mui/material'
import type { WorkshopAuthoringProposal } from '@/types/promptExplorer'
import type { WorkshopContinuationOrigin, WorkshopSavedHandoff } from '@/types/promptExplorer'
import type { FlowProposalApplyResult } from '../FlowBuilder/types'

import type {
  AgentWorkshopContext,
  WorkshopAction,
  CustomAgent,
  PromptCatalog,
  ToolIdeaConversationEntry,
} from '@/types/promptExplorer'
import type { AgentMetadata } from '@/services/agentStudioService'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'

import { useWorkshopDraft } from './useWorkshopDraft'
import {
  buildDiscussDraftMessage,
  buildDiscussPromptMessage,
  buildModelAdviceMessage,
  buildToolRequestMessage,
  describeChangedSections,
  type GettingStartedMode,
  type WorkshopSection,
} from './workshopDraftUtils'
import { NARROW_QUERY } from './workshopStyles'
import WorkshopHeader from './WorkshopHeader'
import WorkshopNav from './WorkshopNav'
import WorkshopOutputSetup from './WorkshopOutputSetup'
import OutputStructureWorkflow from './OutputStructureWorkflow'
import SelectProfileDialog from './dialogs/SelectProfileDialog'
import ProfileRevisionReview from './ProfileRevisionReview'
import SavedExecutionSummary from './SavedExecutionSummary'
import { workshopDraftKey } from '../authoringContext'
import WorkshopStartScreen from './WorkshopStartScreen'
import SetupSection, { type EnvelopeSummary } from './SetupSection'
import PromptSection from './PromptSection'
import ToolsSection from './ToolsSection'
import VersionsSection from './VersionsSection'
import SaveVersionDialog from './dialogs/SaveVersionDialog'
import SaveAsDialog from './dialogs/SaveAsDialog'
import OpenAgentDialog from './dialogs/OpenAgentDialog'
import ManageAgentsDialog from './dialogs/ManageAgentsDialog'
import ToolLibraryDialog from './dialogs/ToolLibraryDialog'
import ToolRequestDialog from './dialogs/ToolRequestDialog'
import {
  DeleteAgentDialog,
  RevertVersionDialog,
  SelfExclusionDialog,
  UnsavedChangesDialog,
} from './dialogs/ConfirmDialogs'

/** Lets the page ask the Workshop whether it may leave (tab switch, programmatic navigation). */
export interface WorkshopLeaveGuard {
  /**
   * Resolves true when the page may leave. A clean draft resolves at once; a dirty
   * draft opens the unsaved-changes dialog and resolves with the curator's choice
   * (Discard = true, Keep editing = false).
   */
  requestLeave: () => Promise<boolean>
}

export interface WorkshopAuthoringContextHandle {
  captureAuthoringContext: () => AgentWorkshopContext
  applyAuthoringProposal: (proposal: WorkshopAuthoringProposal) => Promise<FlowProposalApplyResult>
  runChatAction: (action: WorkshopAction, cloneSource?: CustomAgent) => boolean
}

interface GuardedAction {
  proceed: () => void
  cancel?: () => void
}

interface PromptWorkshopProps {
  catalog: PromptCatalog
  initialChatAction?: WorkshopAction
  initialChatCloneSource?: CustomAgent
  onInitialChatActionComplete?: () => void
  continuationOrigin?: WorkshopContinuationOrigin
  onSavedHandoff?: (handoff: WorkshopSavedHandoff) => void
  initialParentAgentId?: string | null
  initialCustomAgentId?: string | null
  onContextChange?: (context: AgentWorkshopContext) => void
  onVerifyRequest?: (message: string) => void
  opusConversation?: ToolIdeaConversationEntry[]
  /** Open the given system agent in the Agents tab with its Envelope view. */
  onViewEnvelope?: (agentId: string) => void
  /** Receives the leave guard so the page can confirm before navigating away. */
  leaveGuardRef?: Ref<WorkshopLeaveGuard>
  /** Synchronous access to the current draft for send-time AI Chat capture. */
  authoringContextRef?: Ref<WorkshopAuthoringContextHandle>
}

function summarizeEnvelope(metadata: AgentMetadata | undefined): EnvelopeSummary | null {
  const envelope = metadata?.domain_envelope
  if (!envelope) return null
  const objects = envelope.object_definitions
  const objectLabel = objects.length === 1
    ? `${objects[0].display_name} objects`
    : objects.length === 0
      ? `${envelope.display_name} objects`
      : `${objects.length} object types`
  const isValidator = /valid/i.test(metadata?.category || '')
  return {
    status: envelope.status,
    producesLabel: isValidator ? `Validation findings on ${objectLabel}` : `Produces ${objectLabel}`,
    activeChecks: envelope.validation_summary.by_state.active,
    underDevelopment: envelope.validation_summary.by_state.under_development,
  }
}

function PromptWorkshop({
  catalog,
  initialChatAction,
  initialChatCloneSource,
  onInitialChatActionComplete,
  continuationOrigin,
  onSavedHandoff,
  initialParentAgentId,
  initialCustomAgentId,
  onContextChange,
  onVerifyRequest,
  opusConversation = [],
  onViewEnvelope,
  leaveGuardRef,
  authoringContextRef,
}: PromptWorkshopProps) {
  const { agents: agentMetadata } = useAgentMetadata()
  const draft = useWorkshopDraft({
    catalog,
    continuationOrigin,
    onSavedHandoff,
    initialParentAgentId,
    initialCustomAgentId,
    onContextChange,
  })

  const [section, setSection] = useState<WorkshopSection>('setup')
  const [profilePickerBase, setProfilePickerBase] = useState<string | null>(null)
  const visibleSection = section === 'output_structure' && draft.outputDraft.mode !== 'profile_bound_generic' ? 'setup' : section
  const [startScreenRequested, setStartScreenRequested] = useState(
    () => !(initialParentAgentId || '').trim() && !(initialCustomAgentId || '').trim()
  )
  const [focusOriginToken, setFocusOriginToken] = useState(0)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [saveAsDialogOpen, setSaveAsDialogOpen] = useState(false)
  const [openDialogOpen, setOpenDialogOpen] = useState(false)
  const [manageDialogOpen, setManageDialogOpen] = useState(false)
  const [toolLibraryOpen, setToolLibraryOpen] = useState(false)
  const [toolRequestOpen, setToolRequestOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<CustomAgent | null>(null)
  const [pendingRevert, setPendingRevert] = useState<(typeof draft.versions)[number] | null>(null)
  const [pendingGuardedAction, setPendingGuardedAction] = useState<GuardedAction | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const handledInitialChatActionRef = useRef<WorkshopAction | undefined>(undefined)
  const [returnFocusToken, setReturnFocusToken] = useState(0)

  const {
    selectedCustomAgent,
    selectedCustomAgentId,
    selectedTemplate,
    parentAgent,
    parentAgentId,
    gettingStartedMode,
    selectedCloneSource,
    templateMissing,
    dirty,
    isNewDraft,
    versions,
  } = draft

  const showStartScreen = startScreenRequested && !selectedCustomAgentId

  const guard = useCallback((action: () => void) => {
    if (dirty.any) {
      setPendingGuardedAction({ proceed: action })
      return
    }
    action()
  }, [dirty.any])

  const requestLeave = useCallback((): Promise<boolean> => {
    if (!dirty.any) return Promise.resolve(true)
    return new Promise<boolean>((resolve) => {
      setPendingGuardedAction({
        proceed: () => resolve(true),
        cancel: () => {
          setReturnFocusToken((token) => token + 1)
          resolve(false)
        },
      })
    })
  }, [dirty.any])

  useImperativeHandle(leaveGuardRef, () => ({ requestLeave }), [requestLeave])


  // After "Keep editing" on a leave request, the dialog hands focus back to the
  // element that opened it (a page tab). Bring it back into the Workshop instead.
  // This effect runs after the dialog's own focus restore in the same commit.
  useEffect(() => {
    if (returnFocusToken === 0) return
    rootRef.current?.focus()
  }, [returnFocusToken])

  const selectedOutputAgentId = useMemo(() => draft.outputDraft.mode === 'domain'
    ? Object.keys(agentMetadata).find((id) => agentMetadata[id].output_schema_key === draft.outputDraft.schemaKey)
    : undefined, [agentMetadata, draft.outputDraft.mode, draft.outputDraft.schemaKey])
  const envelope = summarizeEnvelope(selectedOutputAgentId ? agentMetadata[selectedOutputAgentId] : undefined)

  const templateLabel = selectedTemplate?.name || parentAgent?.agent_name || ''
  const originLabel = (() => {
    if (showStartScreen) return 'Not saved yet'
    if (selectedCustomAgent) {
      if (!selectedCustomAgent.template_source) return 'From scratch'
      if (templateMissing) return `Template: ${selectedCustomAgent.template_source} (no longer available)`
      return `Template: ${templateLabel || selectedCustomAgent.template_source}`
    }
    const base = gettingStartedMode === 'clone'
      ? `Cloned from ${selectedCloneSource?.name || '(no source)'}`
      : gettingStartedMode === 'scratch'
        ? 'From scratch'
        : `Template: ${templateLabel || '(none selected)'}`
    return `${base} · Not saved yet`
  })()

  // Template/clone provenance lives in the authoring context; it is not the
  // identity of this draft. Prefer the current name, including unsaved renames.
  const targetName = draft.name.trim() || 'this agent draft'
  const targetId = selectedCustomAgent?.agent_id || 'unsaved_draft'

  const handleAskClaude = onVerifyRequest
    ? () => onVerifyRequest(buildDiscussDraftMessage(targetName, targetId, draft.selectedGroupId))
    : undefined
  const handleDiscussPrompt = onVerifyRequest
    ? () => {
      onVerifyRequest(buildDiscussPromptMessage(targetName, targetId, draft.selectedGroupId))
      draft.setStatus('Opened system-prompt discussion with AI Chat')
    }
    : undefined
  const handleAskAboutModels = onVerifyRequest
    ? () => {
      onVerifyRequest(buildModelAdviceMessage(
        targetName,
        draft.modelOptions,
        draft.selectedModelId,
        draft.selectedModelReasoning,
        draft.selectedToolIds
      ))
      draft.setStatus('Opened model-selection discussion with AI Chat')
    }
    : undefined
  const handleAskForTool = onVerifyRequest
    ? () => {
      onVerifyRequest(buildToolRequestMessage(
        targetName,
        targetId,
        draft.selectedToolIds
      ))
      draft.setStatus('Opened tool-ideation discussion with AI Chat')
    }
    : undefined

  const handleNew = () => guard(() => {
    draft.handleNew()
    setStartScreenRequested(true)
    setSection('setup')
  })

  const customExtractionTemplate = draft.templateOptions.find((template) =>
    template.output_contract?.output_mode === 'unprofiled_generic',
  )
  const handleCustomExtraction = () => {
    if (!customExtractionTemplate) return
    draft.startDraft('template', customExtractionTemplate.agent_id)
    setStartScreenRequested(false)
    setSection('output_structure')
  }

  const handleChooseStart = (mode: GettingStartedMode) => {
    draft.startDraft(mode)
    setStartScreenRequested(false)
    setSection('setup')
    setFocusOriginToken((token) => token + 1)
  }

  const handleModeChange = (mode: GettingStartedMode) => guard(() => draft.startDraft(mode))

  const handleTemplateChange = (agentId: string) => {
    if (!selectedCustomAgent) {
      draft.setParentAgentId(agentId)
      return
    }
    guard(() => {
      draft.startDraft('template')
      draft.setParentAgentId(agentId)
    })
  }

  const openAgent = (agentId: string) => guard(() => {
    draft.selectCustomAgent(agentId)
    setStartScreenRequested(false)
    setOpenDialogOpen(false)
  })

  const handleSaveClick = () => {
    if (!draft.canSave) return
    setSaveDialogOpen(true)
  }

  const runChatAction = useCallback((action: WorkshopAction, cloneSource?: CustomAgent): boolean => {
    if (draft.loading || draft.saving || draft.authoringBusy || draft.outputLoading) return false
    const request = action.request
    if (request.action === 'open_agent' || request.action === 'new_agent') {
      const source = action.source
      const custom = source?.agent_id.startsWith('ca_')
        ? (request.mode === 'clone' && cloneSource?.agent_id === source.agent_id ? cloneSource : undefined)
          || draft.customAgents.find((agent) => agent.agent_id === source.agent_id) : undefined
      if (source && (source.agent_id.startsWith('ca_')
        ? !custom || custom.execution_revision_id !== source.agent_revision_id
        : !draft.templateOptions.some((template) => template.agent_id === source.agent_id))) return false
      if (request.action === 'open_agent') {
        if (!custom) return false
        draft.selectCustomAgent(custom.id)
      } else {
        if (!request.mode) return false
        draft.startDraft(request.mode)
        if (request.mode === 'template' && source) draft.setParentAgentId(source.agent_id)
        if (request.mode === 'clone' && custom) {
          draft.setChatCloneSource(custom)
          draft.setCloneSourceAgentId(custom.id)
        }
      }
      setStartScreenRequested(false)
      setSection('setup')
      setFocusOriginToken((token) => token + 1)
      return true
    }
    if (request.action === 'save') {
      if (!draft.canSave) return false
      setSaveDialogOpen(true)
    } else if (request.action === 'save_as') {
      setSaveAsDialogOpen(true)
    } else if (request.action === 'show_section') {
      if (!request.section) return false
      if (request.section === 'tool_request') setToolRequestOpen(true)
      else if (request.section === 'manage') setManageDialogOpen(true)
      else {
        if (request.section === 'output_structure' && draft.outputDraft.mode !== 'profile_bound_generic') return false
        setSection(request.section)
        setStartScreenRequested(false)
      }
    } else return false
    return true
  }, [draft])

  useImperativeHandle(
    authoringContextRef,
    () => ({
      runChatAction,
      captureAuthoringContext: draft.captureAuthoringContext,
      applyAuthoringProposal: async (proposal) => {
        const result = await draft.applyAuthoringProposal(proposal)
        if (result.applied) setStartScreenRequested(false)
        return result
      },
    }),
    [draft.captureAuthoringContext, draft.applyAuthoringProposal, runChatAction]
  )

  useEffect(() => {
    if (!initialChatAction || handledInitialChatActionRef.current === initialChatAction
      || draft.loading || draft.saving || draft.authoringBusy || draft.outputLoading
      || draft.modelOptions.length === 0) return
    handledInitialChatActionRef.current = initialChatAction
    if (!runChatAction(initialChatAction, initialChatCloneSource)) {
      draft.setError('The requested agent changed or is unavailable. Ask AI Chat to reopen it from the current catalog.')
    }
    onInitialChatActionComplete?.()
  }, [initialChatAction, initialChatCloneSource, onInitialChatActionComplete, runChatAction, draft])

  const handleSaveConfirm = (note: string) => {
    setSaveDialogOpen(false)
    void draft.handleSave({ notes: note })
  }

  const handleSaveAsConfirm = (name: string) => {
    setSaveAsDialogOpen(false)
    void draft.handleSave({ forceCreate: true, nameOverride: name })
  }

  const handleDeleteConfirm = () => {
    if (!pendingDelete) return
    const target = pendingDelete
    setPendingDelete(null)
    void draft.handleDeleteById(target).then(() => {
      if (target.id === selectedCustomAgentId) setStartScreenRequested(true)
    })
  }

  const handleRevertConfirm = () => {
    if (pendingRevert === null) return
    const version = pendingRevert
    setPendingRevert(null)
    void draft.handleRevert(version)
  }

  const nextVersion = versions.reduce((max, version) => Math.max(max, version.revision), 0) + 1
  const hasTemplate = Boolean(draft.parentAgent) && (gettingStartedMode !== 'scratch' || Boolean(selectedCustomAgent?.template_source))

  return (
    <Box
      component="fieldset"
      aria-label="Workshop draft"
      disabled={draft.authoringBusy || draft.saving || draft.outputLoading}
      aria-busy={draft.authoringBusy || draft.outputLoading}
      ref={rootRef}
      tabIndex={-1}
      sx={{
        border: 0,
        padding: 0,
        margin: 0,
        minWidth: 0,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        containerType: 'inline-size',
        containerName: 'workshop',
      }}
    >
      <WorkshopHeader
        icon={draft.icon}
        name={showStartScreen ? '' : draft.name}
        originLabel={originLabel}
        saveState={draft.saveState}
        lastSavedAt={draft.lastSavedAt}
        dirty={dirty.any}
        canSave={!showStartScreen && draft.canSave}
        canDelete={Boolean(selectedCustomAgent)}
        saving={draft.saving}
        onOpen={() => setOpenDialogOpen(true)}
        onNew={handleNew}
        onSave={handleSaveClick}
        onSaveAs={() => setSaveAsDialogOpen(true)}
        onManage={() => setManageDialogOpen(true)}
        onDelete={() => {
          if (selectedCustomAgent) setPendingDelete(selectedCustomAgent)
        }}
      />

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: '200px minmax(0, 1fr)',
          [NARROW_QUERY]: { gridTemplateColumns: 'minmax(0, 1fr)', gridTemplateRows: 'auto minmax(0, 1fr)' },
        }}
      >
        <WorkshopNav
          section={visibleSection}
          showOutputStructure={draft.outputDraft.mode === 'profile_bound_generic'}
          onSectionChange={setSection}
          dirty={dirty}
          toolCount={draft.selectedToolIds.length}
          versionCount={versions.length}
          onAskClaude={handleAskClaude}
        />

        <Box
          sx={{
            minWidth: 0,
            minHeight: 0,
            overflow: 'auto',
            px: 3,
            pt: 2.25,
            pb: 3.5,
            display: 'flex',
            flexDirection: 'column',
            gap: 2.5,
          }}
        >
          {draft.error && (
            <Alert severity="error" onClose={() => draft.setError(null)} sx={{ maxWidth: 780 }}>
              {draft.saveState === 'failed' ? (
                <>
                  <strong style={{ fontWeight: 500 }}>Could not save.</strong> {draft.error} Your edits are still here.
                </>
              ) : draft.error}
            </Alert>
          )}
          {draft.status && (
            <Alert severity="success" onClose={() => draft.setStatus(null)} sx={{ maxWidth: 780 }}>
              {draft.status}
            </Alert>
          )}
          {draft.canUndoAuthoringProposal && (
            <Button onClick={() => void draft.undoAuthoringProposal()}>Undo AI changes</Button>
          )}

          {draft.outputLoading ? <Alert severity="info" role="status">Loading the saved executable revision and Output Structure…</Alert>
          : draft.outputLoadError ? <Alert severity="error" action={<Button onClick={draft.retryOutputLoad}>Retry</Button>}>{draft.outputLoadError}</Alert>
          : showStartScreen ? (
            <WorkshopStartScreen
              onChoose={handleChooseStart}
              onCustomExtraction={customExtractionTemplate ? handleCustomExtraction : undefined}
              agents={agentMetadata}
              hasTemplates={draft.templateOptions.length > 0}
              hasSavedAgents={draft.customAgents.length > 0}
            />
          ) : visibleSection === 'setup' ? (
            <>
            <SetupSection
              gettingStartedMode={gettingStartedMode}
              onModeChange={handleModeChange}
              templateOptions={draft.templateOptions}
              parentAgentId={parentAgentId}
              onTemplateChange={handleTemplateChange}
              missingTemplateId={templateMissing ? selectedCustomAgent?.template_source || null : null}
              templateAllowedGroupIds={selectedTemplate?.allowed_group_ids || []}
              customAgents={draft.selectedCloneSource && !draft.customAgents.some((agent) => agent.id === draft.selectedCloneSource?.id)
                ? [...draft.customAgents, draft.selectedCloneSource] : draft.customAgents}
              cloneSourceAgentId={draft.cloneSourceAgentId}
              onCloneSourceChange={draft.setCloneSourceAgentId}
              isExistingAgent={Boolean(selectedCustomAgent)}
              focusOriginToken={focusOriginToken}
              icon={draft.icon}
              iconOptions={draft.iconOptions}
              onIconChange={draft.setIcon}
              name={draft.name}
              onNameChange={draft.setName}
              description={draft.description}
              onDescriptionChange={draft.setDescription}
              envelope={envelope}
              onViewEnvelope={onViewEnvelope && selectedOutputAgentId
                ? () => onViewEnvelope(selectedOutputAgentId)
                : undefined}
              modelOptions={draft.modelOptions}
              selectedModelId={draft.selectedModelId}
              onModelChange={draft.handleModelChange}
              selectedModelOption={draft.selectedModelOption}
              selectedModelReasoning={draft.selectedModelReasoning}
              onReasoningChange={draft.setSelectedModelReasoning}
              reasoningDescription={draft.selectedModelReasoningDescription}
              onAskClaudeAboutModels={handleAskAboutModels}
              visibility={draft.selectedVisibility}
              onVisibilityChange={draft.setSelectedVisibility}
              allowedGroupIds={draft.selectedAllowedGroupIds}
              onAllowedGroupIdsChange={draft.setSelectedAllowedGroupIds}
              selectableGroupOptions={draft.selectableGroupOptions}
              inheritedAllowedGroupIds={draft.inheritedAllowedGroupIds}
            />
            <WorkshopOutputSetup value={draft.outputDraft} onChange={draft.setOutputDraft}
              disabled={draft.authoringBusy || draft.saving || draft.outputLoading}
              agents={agentMetadata} onEditStructure={() => setSection('output_structure')}
              onChooseExisting={() => setProfilePickerBase(workshopDraftKey(draft.captureAuthoringContext()))} />
            {selectedCustomAgent && draft.savedExecutionRevision && draft.savedExecutionRevision.id === selectedCustomAgent.execution_revision_id
              && draft.savedExecutionRevision.agent_id === selectedCustomAgent.id
              && <SavedExecutionSummary revision={draft.savedExecutionRevision} />}
            </>
          ) : visibleSection === 'output_structure' && draft.outputDraft.profileContract ? (
            <>
            {draft.outputDraft.profilePin && <Typography variant="body2" color="text.secondary">
              Saved version {draft.outputDraft.profilePin.revision}.{' '}
              {selectedCustomAgent && draft.profileCanEdit
                ? 'Your changes will be saved as a new version. Other agents and flows keep their current settings.'
                : 'Saving creates your own copy of this structure.'}
            </Typography>}
            <OutputStructureWorkflow value={draft.outputDraft.profileContract}
              onAskAI={onVerifyRequest ? () => onVerifyRequest(`Help me design the information collected by ${targetName} (${targetId}). Focus on the current output structure draft. Inspect my current draft, including its item guidance, detail names and parts. Help me add or edit details and parts using the current simple design. Ask a question only when my intent is unclear; otherwise propose concrete changes for review. Preserve unrelated settings and my earlier prompt.`) : undefined}
              disabled={draft.authoringBusy || draft.saving || draft.outputLoading}
              onChange={(profileContract) => draft.setOutputDraft({ ...draft.outputDraft, profileContract })}
              onValidate={() => { void draft.validateOutputProfile() }}
              issues={draft.profileIssues} validating={draft.profileValidating} />
            {draft.outputDraft.profilePin && <Box component="details" className="collection-disclosure"><summary>Version history &amp; other uses</summary><ProfileRevisionReview key={draft.outputDraft.profilePin.profile_id}
              disabled={draft.authoringBusy || draft.saving || draft.outputLoading}
              value={draft.outputDraft} onLoadRevision={draft.selectOutputProfile}
              onMakeCopy={() => draft.setOutputDraft({ ...draft.outputDraft, profilePin: null })} /></Box>}
            </>
          ) : section === 'prompt' ? (
            <PromptSection
              parentCorePrompt={draft.parentCorePrompt}
              parentGeneratedContract={draft.parentGeneratedContract}
              parentBasePrompt={draft.parentBasePrompt}
              hasTemplate={hasTemplate}
              templateName={templateLabel}
              customPrompt={draft.customPrompt}
              onCustomPromptChange={draft.setCustomPrompt}
              onResetToTemplate={draft.resetCustomPromptToTemplate}
              overlayStatus={draft.overlayStatus}
              overlayWarning={draft.overlayWarning}
              availableGroupIds={draft.availableGroupIds}
              selectedGroupId={draft.selectedGroupId}
              onGroupChange={draft.setGroupId}
              groupPromptOverrides={draft.groupPromptOverrides}
              selectedGroupPrompt={draft.selectedGroupPrompt}
              hasSelectedGroupOverride={draft.hasSelectedGroupOverride}
              onGroupPromptChange={draft.handleSelectedGroupPromptChange}
              onResetGroupPrompt={draft.handleResetSelectedGroupPrompt}
              includeGroupRules={draft.includeGroupRules}
              onIncludeGroupRulesChange={draft.setIncludeGroupRules}
              loggedInAsLabel={draft.loggedInAsLabel}
              loggedInGroupIds={draft.loggedInGroupIds}
              onDiscussPromptWithClaude={handleDiscussPrompt}
            />
          ) : section === 'tools' ? (
            <ToolsSection
              selectedToolIds={draft.selectedToolIds}
              toolLibrary={draft.toolLibrary}
              onRemoveTool={draft.removeTool}
              onAddTools={() => setToolLibraryOpen(true)}
              hasTemplate={hasTemplate}
              requests={draft.toolIdeaRequests}
              requestsLoading={draft.toolIdeasLoading}
              onNewRequest={() => setToolRequestOpen(true)}
              onAskClaudeToDraft={handleAskForTool}
            />
          ) : (
            <VersionsSection
              versions={versions}
              currentRevisionId={selectedCustomAgent?.execution_revision_id}
              hasAgent={Boolean(selectedCustomAgent)}
              saving={draft.saving}
              loading={draft.versionsLoading}
              error={draft.versionsError}
              hasMore={draft.hasMoreVersions}
              onLoadMore={draft.loadMoreVersions}
              onRetry={draft.retryVersions}
              onRevert={(version) => guard(() => setPendingRevert(version))}
            />
          )}
        </Box>
      </Box>

      <SaveVersionDialog
        open={saveDialogOpen}
        agentName={draft.name}
        nextVersion={isNewDraft ? 1 : nextVersion}
        isNewAgent={isNewDraft}
        changedSections={describeChangedSections(dirty)}
        saving={draft.saving}
        onConfirm={handleSaveConfirm}
        onClose={() => setSaveDialogOpen(false)}
      />
      <SaveAsDialog
        open={saveAsDialogOpen}
        initialName={(draft.name || selectedCustomAgent?.name || '').trim() ? `${(draft.name || selectedCustomAgent?.name || '').trim()} (Copy)` : ''}
        saving={draft.saving}
        onConfirm={handleSaveAsConfirm}
        onClose={() => setSaveAsDialogOpen(false)}
      />
      <OpenAgentDialog
        open={openDialogOpen}
        agents={draft.customAgents}
        loading={draft.loading}
        selectedAgentId={selectedCustomAgentId}
        onSelect={openAgent}
        onClose={() => setOpenDialogOpen(false)}
      />
      <ManageAgentsDialog
        open={manageDialogOpen}
        agents={draft.customAgents}
        loading={draft.loading}
        saving={draft.saving}
        selectedAgentId={selectedCustomAgentId}
        onOpenAgent={(agentId) => {
          setManageDialogOpen(false)
          openAgent(agentId)
        }}
        onDeleteAgent={setPendingDelete}
        onClose={() => setManageDialogOpen(false)}
      />
      <ToolLibraryDialog
        open={toolLibraryOpen}
        tools={draft.toolLibrary}
        attachedToolIds={draft.selectedToolIds}
        onConfirm={(toolIds) => {
          draft.applyToolSelection(toolIds)
          setToolLibraryOpen(false)
        }}
        onClose={() => setToolLibraryOpen(false)}
      />
      <ToolRequestDialog
        open={toolRequestOpen}
        submitting={draft.toolIdeaSubmitting}
        onSubmit={(title, description) => draft.submitToolIdea(title, description, opusConversation)}
        onClose={() => setToolRequestOpen(false)}
      />
      <SelfExclusionDialog
        open={Boolean(draft.selfExclusionPrompt)}
        allowedGroupIds={draft.selectedAllowedGroupIds}
        currentUserGroupIds={draft.currentUserGroupIds}
        onConfirm={draft.confirmSelfExclusion}
        onCancel={draft.cancelSelfExclusion}
      />
      <DeleteAgentDialog
        open={Boolean(pendingDelete)}
        agentName={pendingDelete?.name || ''}
        saving={draft.saving}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setPendingDelete(null)}
      />
      <RevertVersionDialog
        open={pendingRevert !== null}
        version={pendingRevert?.revision ?? null}
        saving={draft.saving}
        onConfirm={handleRevertConfirm}
        onCancel={() => setPendingRevert(null)}
      />
      {profilePickerBase !== null && <SelectProfileDialog onClose={() => setProfilePickerBase(null)}
        onSelect={(profile) => {
          if (profilePickerBase !== workshopDraftKey(draft.captureAuthoringContext()) || draft.authoringBusy || draft.saving) {
            draft.setError('The Workshop draft changed while selecting a structure. Your edits are preserved; reopen the picker to try again.')
          } else {
            draft.selectOutputProfile(profile)
            setSection('output_structure')
          }
          setProfilePickerBase(null)
        }} />}
      <UnsavedChangesDialog
        open={Boolean(pendingGuardedAction)}
        onDiscard={() => {
          const action = pendingGuardedAction
          setPendingGuardedAction(null)
          action?.proceed()
        }}
        onKeepEditing={() => {
          const action = pendingGuardedAction
          setPendingGuardedAction(null)
          action?.cancel?.()
        }}
      />
    </Box>
  )
}

export default PromptWorkshop
