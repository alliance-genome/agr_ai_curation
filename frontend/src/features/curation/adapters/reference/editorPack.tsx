import type { ReactNode } from 'react'

import {
  MenuItem,
  TextField,
} from '@mui/material'

import type { CurationDraftField } from '@/features/curation/types'
import type { FieldRowInputProps } from '@/features/curation/editor'
import { areDraftFieldValuesEqual } from '@/features/curation/workspace/workspaceState'
import type { CurationAdapterEditorPack } from '../types'
import {
  REFERENCE_ADAPTER_KEY,
  REFERENCE_FIELD_LAYOUT,
  REFERENCE_FIELD_LAYOUT_BY_KEY,
} from './fieldLayout'

interface ResolvedFieldOption {
  key: string
  label: string
  value: unknown
}

function normalizeFieldTextValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }

  if (typeof value === 'string') {
    return value
  }

  if (
    typeof value === 'number'
    || typeof value === 'boolean'
  ) {
    return String(value)
  }

  return JSON.stringify(value, null, 2)
}

function resolvePlaceholder(field: CurationDraftField): string | undefined {
  const placeholder = field.metadata.placeholder
  return typeof placeholder === 'string' && placeholder.length > 0
    ? placeholder
    : undefined
}

function resolveHelperText(field: CurationDraftField): string | undefined {
  const helperText = field.metadata.helper_text
  return typeof helperText === 'string' && helperText.length > 0
    ? helperText
    : undefined
}

function resolveFieldOptions(
  field: CurationDraftField,
  currentValue: unknown,
): ResolvedFieldOption[] {
  if (field.field_type === 'boolean') {
    const options: ResolvedFieldOption[] = []

    if (!field.required || currentValue === null || currentValue === undefined) {
      options.push({
        key: 'unset',
        label: 'Unset',
        value: null,
      })
    }

    options.push(
      {
        key: 'true',
        label: 'True',
        value: true,
      },
      {
        key: 'false',
        label: 'False',
        value: false,
      },
    )

    return options
  }

  const rawOptions = field.metadata.options
  if (!Array.isArray(rawOptions) || rawOptions.length === 0) {
    return []
  }

  return rawOptions.map((option, index) => {
    if (
      option !== null
      && typeof option === 'object'
      && !Array.isArray(option)
      && 'value' in option
    ) {
      const label = typeof option.label === 'string'
        ? option.label
        : normalizeFieldTextValue(option.value)

      return {
        key: `option-${index}`,
        label,
        value: option.value,
      }
    }

    return {
      key: `option-${index}`,
      label: normalizeFieldTextValue(option),
      value: option,
    }
  })
}

function stringifyAuthorList(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((entry) => (typeof entry === 'string' ? entry.trim() : ''))
      .join('\n')
  }

  if (typeof value === 'string') {
    return value
  }

  return ''
}

function parseAuthorList(value: string): string[] {
  return value.split('\n').map((entry) => entry.trim())
}

function renderAuthorListInput({
  ariaLabel,
  disabled,
  field,
  inputId,
  onChange,
  value,
}: FieldRowInputProps): ReactNode {
  return (
    <TextField
      data-testid={`field-input-${field.field_key}`}
      disabled={disabled}
      fullWidth
      helperText={resolveHelperText(field) ?? 'One author per line.'}
      id={inputId}
      inputProps={{ 'aria-label': ariaLabel }}
      minRows={3}
      multiline
      onChange={(event) => onChange(parseAuthorList(event.target.value))}
      placeholder={resolvePlaceholder(field)}
      size="small"
      value={stringifyAuthorList(value)}
    />
  )
}

function renderDefaultReferenceInput({
  ariaLabel,
  disabled,
  field,
  inputId,
  onChange,
  value,
}: FieldRowInputProps): ReactNode {
  const options = resolveFieldOptions(field, value)
  const placeholder = resolvePlaceholder(field)

  if (options.length > 0) {
    const selectedKey =
      options.find((option) => areDraftFieldValuesEqual(option.value, value ?? null))?.key ?? ''

    return (
      <TextField
        data-testid={`field-input-${field.field_key}`}
        disabled={disabled}
        fullWidth
        id={inputId}
        inputProps={{ 'aria-label': ariaLabel }}
        onChange={(event) => {
          const nextOption = options.find((option) => option.key === event.target.value)
          onChange(nextOption?.value ?? null)
        }}
        select
        size="small"
        value={selectedKey}
      >
        {options.map((option) => (
          <MenuItem key={option.key} value={option.key}>
            {option.label}
          </MenuItem>
        ))}
      </TextField>
    )
  }

  if (field.field_type === 'json') {
    return (
      <TextField
        data-testid={`field-input-${field.field_key}`}
        disabled={disabled}
        fullWidth
        id={inputId}
        inputProps={{ 'aria-label': ariaLabel }}
        minRows={3}
        multiline
        onChange={(event) => {
          const nextValue = event.target.value

          if (nextValue.trim().length === 0) {
            onChange(null)
            return
          }

          try {
            onChange(JSON.parse(nextValue))
          } catch {
            onChange(nextValue)
          }
        }}
        placeholder={placeholder}
        size="small"
        value={normalizeFieldTextValue(value)}
      />
    )
  }

  if (field.field_type === 'number') {
    return (
      <TextField
        data-testid={`field-input-${field.field_key}`}
        disabled={disabled}
        fullWidth
        id={inputId}
        inputProps={{
          'aria-label': ariaLabel,
          inputMode: 'decimal',
          step: 'any',
        }}
        onChange={(event) => {
          const nextValue = event.target.value

          if (nextValue.length === 0) {
            onChange(null)
            return
          }

          const parsedValue = Number(nextValue)
          onChange(Number.isFinite(parsedValue) ? parsedValue : nextValue)
        }}
        placeholder={placeholder}
        size="small"
        type="number"
        value={normalizeFieldTextValue(value)}
      />
    )
  }

  return (
    <TextField
      data-testid={`field-input-${field.field_key}`}
      disabled={disabled}
      fullWidth
      id={inputId}
      inputProps={{
        'aria-label': ariaLabel,
        readOnly: field.read_only,
      }}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      size="small"
      value={normalizeFieldTextValue(value)}
    />
  )
}

export function renderReferenceFieldInput(props: FieldRowInputProps): ReactNode {
  const layout = REFERENCE_FIELD_LAYOUT_BY_KEY.get(props.field.field_key)
  const widget = layout?.widget
    ?? (typeof props.field.metadata.widget === 'string' ? props.field.metadata.widget : undefined)

  if (widget === 'reference_author_list') {
    return renderAuthorListInput(props)
  }

  return renderDefaultReferenceInput(props)
}

export const referenceEditorPack: CurationAdapterEditorPack = {
  adapterKey: REFERENCE_ADAPTER_KEY,
  fieldLayout: REFERENCE_FIELD_LAYOUT,
  renderFieldInput: renderReferenceFieldInput,
}
