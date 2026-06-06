import type { ReactNode } from 'react'

import {
  Box,
  MenuItem,
  TextField,
  Typography,
} from '@mui/material'

import type { CurationDraftField } from '../types'
import {
  areDraftFieldValuesEqual,
  resolveEnvelopeFieldPath,
} from '../workspace/workspaceState'
import {
  ChipFieldValue,
  CurieChipFieldValue,
  DivergenceFieldValue,
  EvidenceLocatorFieldValue,
  resolveRenderAs,
  SubTableFieldValue,
} from './fieldRenderers'

const fieldInputSx = {
  '& .MuiOutlinedInput-root': {
    backgroundColor: 'rgba(2, 9, 21, 0.5)',
    borderRadius: 1,
    color: 'rgba(255, 255, 255, 0.9)',
    transition: 'background-color 160ms ease, border-color 160ms ease',
    '& fieldset': {
      borderColor: 'rgba(255, 255, 255, 0.12)',
    },
    '&:hover fieldset': {
      borderColor: 'rgba(100, 181, 246, 0.38)',
    },
    '&.Mui-focused fieldset': {
      borderColor: '#2196f3',
    },
    '&.Mui-disabled': {
      backgroundColor: 'rgba(255, 255, 255, 0.035)',
    },
  },
  '& .MuiOutlinedInput-input': {
    paddingTop: '6px',
    paddingBottom: '6px',
  },
  '& .MuiInputBase-input': {
    fontSize: '0.84rem',
  },
  '& .MuiInputBase-input.Mui-disabled': {
    WebkitTextFillColor: 'rgba(255, 255, 255, 0.58)',
  },
}

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
  labelSubtitleSlot?: ReactNode
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
  const renderAs = resolveRenderAs(field)
  const useReadOnlyPresentation = disabled

  if (useReadOnlyPresentation && renderAs === 'chip') {
    return <ChipFieldValue value={value} />
  }

  if (useReadOnlyPresentation && (renderAs === 'curie-chip' || renderAs === 'term-chip')) {
    return <CurieChipFieldValue value={value} />
  }

  if (useReadOnlyPresentation && renderAs === 'sub-table') {
    return <SubTableFieldValue value={value} />
  }

  if (useReadOnlyPresentation && renderAs === 'evidence-locator') {
    return <EvidenceLocatorFieldValue value={value} />
  }

  if (useReadOnlyPresentation && renderAs === 'notes') {
    return (
      <Typography
        color="text.secondary"
        sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
        variant="body2"
      >
        {normalizeFieldTextValue(value)}
      </Typography>
    )
  }

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
        sx={fieldInputSx}
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

  if (field.field_type === 'json' || field.field_type === 'object' || field.field_type === 'array') {
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
        sx={fieldInputSx}
        value={normalizeFieldTextValue(value)}
      />
    )
  }

  if (field.field_type === 'number' || field.field_type === 'integer') {
    const integerField = field.field_type === 'integer'

    return (
      <TextField
        data-testid={`field-input-${field.field_key}`}
        disabled={disabled}
        fullWidth
        id={inputId}
        inputProps={{
          'aria-label': ariaLabel,
          inputMode: integerField ? 'numeric' : 'decimal',
          step: integerField ? 1 : 'any',
        }}
        onChange={(event) => {
          const nextValue = event.target.value

          if (nextValue.length === 0) {
            onChange(null)
            return
          }

          const parsedValue = Number(nextValue)
          if (!Number.isFinite(parsedValue)) {
            onChange(nextValue)
            return
          }

          if (integerField && !Number.isInteger(parsedValue)) {
            onChange(nextValue)
            return
          }

          onChange(parsedValue)
        }}
        placeholder={placeholder}
        size="small"
        sx={fieldInputSx}
        type="number"
        value={normalizeFieldTextValue(value)}
      />
    )
  }

  const defaultInput = (
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
      sx={fieldInputSx}
      value={normalizeFieldTextValue(value)}
    />
  )

  if (renderAs === 'divergence') {
    return (
      <>
        {defaultInput}
        <DivergenceFieldValue proposedValue={field.seed_value} value={value} />
      </>
    )
  }

  return defaultInput
}

export default function FieldRow({
  field,
  value,
  validationSlot,
  evidenceSlot,
  revertSlot,
  labelSubtitleSlot,
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
      data-field-key={field.field_key}
      data-field-path={resolveEnvelopeFieldPath(field)}
      data-testid={`field-row-${field.field_key}`}
      sx={{
        display: 'grid',
        gridTemplateColumns: {
          xs: '1fr',
          md: 'minmax(128px, 0.34fr) minmax(0, 1fr)',
        },
        columnGap: 1.25,
        rowGap: 0.15,
        alignItems: {
          xs: 'stretch',
          md: 'start',
        },
      }}
    >
      <Box
        sx={{
          alignSelf: {
            xs: 'flex-start',
            md: 'flex-start',
          },
          pt: {
            xs: 0,
            md: 0.5,
          },
        }}
      >
        <Typography
          color="text.secondary"
          component="label"
          htmlFor={inputId}
          sx={{
            display: 'block',
            fontWeight: 600,
            lineHeight: 1.25,
            textAlign: 'left',
          }}
          variant="body2"
        >
          {field.label}
        </Typography>
        {labelSubtitleSlot}
      </Box>

      <Box
        sx={{
          alignItems: 'start',
          columnGap: 1,
          display: 'grid',
          gridTemplateColumns: '1fr',
          minWidth: 0,
          rowGap: 0.45,
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          {renderInput ? renderInput(inputProps) : renderDefaultInput(inputProps)}
        </Box>
        <Box
          data-testid={`field-validation-slot-${field.field_key}`}
          sx={{
            alignItems: 'center',
            display: validationSlot ? 'flex' : 'none',
            justifyContent: 'flex-start',
            minWidth: 0,
            mt: 0.15,
            '&:empty': { display: 'none' },
          }}
        >
          {validationSlot}
        </Box>
      </Box>

      <Box
        data-testid={`field-evidence-slot-${field.field_key}`}
        sx={{
          gridColumn: { md: '2' },
          display: evidenceSlot ? 'flex' : 'none',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 0.5,
          mt: evidenceSlot ? 0.25 : 0,
          '&:empty': { display: 'none', marginTop: 0 },
        }}
      >
        {evidenceSlot}
      </Box>

      <Box
        data-testid={`field-revert-slot-${field.field_key}`}
        sx={{
          gridColumn: { md: '2' },
          display: revertSlot ? 'flex' : 'none',
          alignItems: 'center',
          justifyContent: {
            xs: 'flex-start',
            md: 'flex-end',
          },
          '&:empty': { display: 'none' },
        }}
      >
        {revertSlot}
      </Box>
    </Box>
  )
}
