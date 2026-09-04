import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from 'react'
import { Alert, Box, Button } from '@mui/material'
import type { WorkshopAuthoringProposal } from '@/types/promptExplorer'
import type { WorkshopContinuationOrigin, WorkshopSavedHandoff } from '@/types/promptExplorer'
import type { FlowProposalApplyResult } from '../FlowBuilder/types'

import type {
  AgentWorkshopContext,
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
}

interface GuardedAction {
  proceed: () => void
  cancel?: () => void
}

interface PromptWorkshopProps {
  catalog: PromptCatalog
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
  useImperativeHandle(
    authoringContextRef,
    () => ({
      captureAuthoringContext: draft.captureAuthoringContext,
      applyAuthoringProposal: async (proposal) => {
        const result = await draft.applyAuthoringProposal(proposal)
        if (result.applied) setStartScreenRequested(false)
        return result
      },
    }),
    [draft.captureAuthoringContext, draft.applyAuthoringProposal]
  )

  // After "Keep editing" on a leave request, the dialog hands focus back to the
  // element that opened it (a page tab). Bring it back into the Workshop instead.
  // This effect runs after the dialog's own focus restore in the same commit.
  useEffect(() => {
    if (returnFocusToken === 0) return
    rootRef.current?.focus()
  }, [returnFocusToken])

  const envelope = useMemo(
    () => summarizeEnvelope(draft.domainEnvelopeAgentId ? agentMetadata[draft.domainEnvelopeAgentId] : undefined),
    [agentMetadata, draft.domainEnvelopeAgentId]
  )

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

  const targetName = selectedCustomAgent?.name || draft.name.trim() || selectedTemplate?.name || parentAgent?.agent_name || 'this agent draft'
  const targetId = selectedCustomAgent?.agent_id || parentAgentId || 'unknown'

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
        selectedCustomAgent?.agent_id || parentAgentId || 'unsaved_draft',
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
      disabled={draft.authoringBusy || draft.saving}
      aria-busy={draft.authoringBusy}
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
          section={section}
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

          {showStartScreen ? (
            <WorkshopStartScreen
              onChoose={handleChooseStart}
              hasTemplates={draft.templateOptions.length > 0}
              hasSavedAgents={draft.customAgents.length > 0}
            />
          ) : section === 'setup' ? (
            <SetupSection
              gettingStartedMode={gettingStartedMode}
              onModeChange={handleModeChange}
              templateOptions={draft.templateOptions}
              parentAgentId={parentAgentId}
              onTemplateChange={handleTemplateChange}
              missingTemplateId={templateMissing ? selectedCustomAgent?.template_source || null : null}
              templateAllowedGroupIds={selectedTemplate?.allowed_group_ids || []}
              customAgents={draft.customAgents}
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
              onViewEnvelope={onViewEnvelope && draft.domainEnvelopeAgentId
                ? () => onViewEnvelope(draft.domainEnvelopeAgentId)
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
