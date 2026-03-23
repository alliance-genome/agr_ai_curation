import type { ReactNode } from 'react'

import {
  Box,
  MenuItem,
  TextField,
  Typography,
} from '@mui/material'

import type { CurationDraftField } from '../types'
import { areDraftFieldValuesEqual } from '../workspace/workspaceState'

interface ResolvedFieldOption {
  key: string
  label: string
  value: unknown
}

export interface FieldRowInputProps {
  ariaLabel: string
  disabled: boolean
  field: CurationDraftField
  inputId: string
  onChange: (value: unknown) => void
  value: unknown
}

export interface FieldRowProps {
  field: CurationDraftField
  value?: unknown
  validationSlot?: ReactNode
  evidenceSlot?: ReactNode
  revertSlot?: ReactNode
  renderInput?: (props: FieldRowInputProps) => ReactNode
  onChange: (value: unknown) => void
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

function renderDefaultInput({
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

export default function FieldRow({
  field,
  value,
  validationSlot,
  evidenceSlot,
  revertSlot,
  renderInput,
  onChange,
}: FieldRowProps) {
  const resolvedValue = value ?? null
  const inputId = `annotation-editor-field-${field.field_key}`
  const inputProps: FieldRowInputProps = {
    ariaLabel: field.label,
    disabled: field.read_only,
    field,
    inputId,
    onChange,
    value: resolvedValue,
  }

  return (
    <Box
      data-testid={`field-row-${field.field_key}`}
      sx={{
        display: 'grid',
        gridTemplateColumns: {
          xs: '1fr',
          md: '84px minmax(0, 1fr) auto auto auto',
        },
        gap: 1.25,
        alignItems: {
          xs: 'stretch',
          md: 'center',
        },
      }}
    >
      <Typography
        color="text.secondary"
        component="label"
        htmlFor={inputId}
        sx={{
          alignSelf: {
            xs: 'flex-start',
            md: 'center',
          },
          pt: {
            xs: 0,
            md: 0.5,
          },
          textAlign: {
            xs: 'left',
            md: 'right',
          },
        }}
        variant="body2"
      >
        {field.label}
      </Typography>

      <Box sx={{ minWidth: 0 }}>
        {renderInput ? renderInput(inputProps) : renderDefaultInput(inputProps)}
      </Box>

      <Box
        data-testid={`field-validation-slot-${field.field_key}`}
        sx={{
          minHeight: 24,
          display: 'flex',
          alignItems: 'center',
          justifyContent: {
            xs: 'flex-start',
            md: 'center',
          },
        }}
      >
        {validationSlot}
      </Box>

      <Box
        data-testid={`field-evidence-slot-${field.field_key}`}
        sx={{
          minHeight: 24,
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 0.75,
        }}
      >
        {evidenceSlot}
      </Box>

      <Box
        data-testid={`field-revert-slot-${field.field_key}`}
        sx={{
          minHeight: 24,
          display: 'flex',
          alignItems: 'center',
          justifyContent: {
            xs: 'flex-start',
            md: 'flex-end',
          },
        }}
      >
        {revertSlot}
      </Box>
    </Box>
  )
}
