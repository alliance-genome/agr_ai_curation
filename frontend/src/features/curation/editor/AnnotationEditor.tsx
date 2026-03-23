import type { ReactNode } from 'react'

import {
  Box,
  Divider,
  Stack,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import type {
  CurationDraftField,
  CurationDraftFieldChange,
} from '../types'
import { useCurationWorkspaceContext } from '../workspace/CurationWorkspaceContext'
import FieldRow, { type FieldRowInputProps } from './FieldRow'
import { useEditorState } from './useEditorState'

interface FieldSection {
  key: string
  label: string
  order: number
  fields: CurationDraftField[]
}

const EMPTY_FIELDS: CurationDraftField[] = []

export interface AnnotationEditorRevertSlotProps {
  canRevert: boolean
  revert: () => void
}

export interface AnnotationEditorProps {
  emptyState?: ReactNode
  onFieldChange?: (
    change: CurationDraftFieldChange,
    field: CurationDraftField,
  ) => void
  renderEvidence?: (field: CurationDraftField) => ReactNode
  renderFieldInput?: (props: FieldRowInputProps) => ReactNode
  renderRevert?: (
    field: CurationDraftField,
    props: AnnotationEditorRevertSlotProps,
  ) => ReactNode
  renderValidation?: (field: CurationDraftField) => ReactNode
}

function humanizeGroupLabel(groupKey?: string | null): string {
  if (!groupKey) {
    return 'Details'
  }

  return groupKey
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase())
}

function buildSections(fields: CurationDraftField[]): FieldSection[] {
  const sections = new Map<string, FieldSection>()

  for (const field of fields) {
    const key = field.group_key ?? '__ungrouped__'
    const existingSection = sections.get(key)

    if (!existingSection) {
      sections.set(key, {
        key,
        label: field.group_label?.trim() || humanizeGroupLabel(field.group_key),
        order: field.order,
        fields: [field],
      })
      continue
    }

    existingSection.fields.push(field)
    existingSection.order = Math.min(existingSection.order, field.order)
    if (!existingSection.label && field.group_label?.trim()) {
      existingSection.label = field.group_label.trim()
    }
  }

  return [...sections.values()].sort(
    (left, right) =>
      left.order - right.order ||
      left.label.localeCompare(right.label) ||
      left.key.localeCompare(right.key),
  )
}

function DefaultEmptyState({ message }: { message: string }) {
  return (
    <Box
      sx={(theme) => ({
        minHeight: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        px: 2.5,
        py: 4,
        textAlign: 'center',
        borderRadius: 1.5,
        border: `1px dashed ${alpha(theme.palette.divider, 0.8)}`,
      })}
    >
      <Typography color="text.secondary" variant="body2">
        {message}
      </Typography>
    </Box>
  )
}

export default function AnnotationEditor({
  emptyState,
  onFieldChange,
  renderEvidence,
  renderFieldInput,
  renderRevert,
  renderValidation,
}: AnnotationEditorProps) {
  const { activeCandidate } = useCurationWorkspaceContext()
  const draft = activeCandidate?.draft ?? null
  const editorState = useEditorState({
    candidateId: activeCandidate?.candidate_id ?? null,
    fields: draft?.fields ?? EMPTY_FIELDS,
  })
  const sections = buildSections(editorState.fields)
  const editorTitle =
    draft?.title ??
    activeCandidate?.display_label ??
    activeCandidate?.candidate_id ??
    'Annotation draft'
  const editorSummary =
    draft?.summary ??
    activeCandidate?.conversation_summary ??
    null

  if (!activeCandidate) {
    return (
      <Box sx={{ flex: 1, minHeight: 0, p: 2 }}>
        {emptyState ?? <DefaultEmptyState message="Select a candidate to begin editing." />}
      </Box>
    )
  }

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        px: 2,
        py: 1.75,
      }}
    >
      <Stack spacing={2.25}>
        <Stack spacing={0.75}>
          <Typography variant="h6">
            {editorTitle}
          </Typography>
          {editorSummary ? (
            <Typography color="text.secondary" variant="body2">
              {editorSummary}
            </Typography>
          ) : null}
        </Stack>

        {sections.length === 0 ? (
          emptyState ?? (
            <DefaultEmptyState message="No editable fields are available for this candidate." />
          )
        ) : (
          sections.map((section) => (
            <Box
              data-testid={`annotation-editor-section-${section.key}`}
              key={section.key}
              sx={(theme) => ({
                borderRadius: 1.5,
                border: `1px solid ${alpha(theme.palette.divider, 0.8)}`,
                backgroundColor: alpha(theme.palette.background.paper, 0.44),
                overflow: 'hidden',
              })}
            >
              <Box
                sx={(theme) => ({
                  px: 1.5,
                  py: 1.25,
                  borderBottom: `1px solid ${alpha(theme.palette.divider, 0.8)}`,
                  backgroundColor: alpha(theme.palette.background.default, 0.34),
                })}
              >
                <Typography
                  color="text.secondary"
                  letterSpacing="0.08em"
                  variant="overline"
                >
                  {section.label.toUpperCase()}
                </Typography>
              </Box>

              <Stack
                divider={<Divider flexItem />}
                spacing={0}
              >
                {section.fields.map((field) => (
                  <Box
                    key={field.field_key}
                    sx={{
                      px: 1.5,
                      py: 1.25,
                    }}
                  >
                    <FieldRow
                      evidenceSlot={renderEvidence?.(field)}
                      field={field}
                      onChange={(value) => {
                        editorState.setFieldValue(field.field_key, value)
                        onFieldChange?.(
                          {
                            field_key: field.field_key,
                            value: value ?? null,
                          },
                          field,
                        )
                      }}
                      renderInput={renderFieldInput}
                      revertSlot={renderRevert?.(field, {
                        canRevert: field.dirty,
                        revert: () => {
                          editorState.revertField(field.field_key)
                          onFieldChange?.(
                            {
                              field_key: field.field_key,
                              revert_to_seed: true,
                            },
                            field,
                          )
                        },
                      })}
                      validationSlot={renderValidation?.(field)}
                      value={field.value}
                    />
                  </Box>
                ))}
              </Stack>
            </Box>
          ))
        )}
      </Stack>
    </Box>
  )
}
