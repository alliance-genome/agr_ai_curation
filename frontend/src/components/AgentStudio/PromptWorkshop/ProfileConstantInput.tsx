import { Button, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from '@mui/material'
import type { GenericProfileValueSchema } from '@/services/genericProfileService'

interface Props {
  label: string
  schema: GenericProfileValueSchema
  nullable: boolean
  value: unknown
  onChange: (value: unknown) => void
  onBlur: () => void
  disabled?: boolean
}

/** Compare decimal values across ordinary/exponent spelling without rounding. */
function decimalIdentity(raw: string): string {
  const [mantissa, exponent = '0'] = raw.toLowerCase().split('e')
  const [whole, fraction = ''] = mantissa.replace(/^[+-]/, '').split('.')
  const digits = (whole + fraction).replace(/^0+/, '')
  if (!digits) return '0'
  const coefficient = digits.replace(/0+$/, '')
  const power = BigInt(exponent) + BigInt(digits.length - coefficient.length - fraction.length)
  return `${mantissa.startsWith('-') ? '-' : ''}${coefficient}e${power}`
}

/** Typed constant controls share the parent mapping draft, never a JSON editor. */
export default function ProfileConstantInput({ label, schema, nullable, value, onChange, onBlur, disabled = false }: Props) {
  let control
  if (schema.kind === 'object') {
    const object = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
    control = <Stack component="fieldset" disabled={disabled} spacing={1}><Typography component="legend">{label}</Typography>
      {schema.fields.map((field) => <Stack key={field.key} spacing={1}>
        {!field.required && <FormControlLabel label={`Include ${field.display_name || field.key}`} control={<Checkbox
          checked={Object.hasOwn(object, field.key)} onChange={(_, checked) => {
            const next = { ...object }
            if (checked) next[field.key] = undefined
            else delete next[field.key]
            onChange(next)
          }} />} />}
        {(field.required || Object.hasOwn(object, field.key)) && <ProfileConstantInput label={`${label} · ${field.display_name || field.key}`}
          schema={field.value_schema} nullable={field.nullable ?? false} value={object[field.key]} disabled={disabled}
          onChange={(next) => onChange({ ...object, [field.key]: next })} onBlur={onBlur} />}
      </Stack>)}
    </Stack>
  } else if (schema.kind === 'array') {
    const array = Array.isArray(value) ? value : []
    control = <Stack component="fieldset" disabled={disabled} spacing={1}><Typography component="legend">{label}</Typography>
      {array.map((item, index) => <Stack key={index} spacing={1}>
        <ProfileConstantInput label={`${label} · item ${index + 1}`} schema={schema.items} nullable={false} value={item} disabled={disabled}
          onChange={(next) => onChange(array.map((old, i) => i === index ? next : old))} onBlur={onBlur} />
        <Button onClick={() => onChange(array.filter((_, i) => i !== index))}>Remove item {index + 1}</Button>
      </Stack>)}
      <Button onClick={() => onChange([...array, null])}>Add item to {label}</Button>
      {value === undefined && <Button onClick={() => onChange([])}>Use an empty list for {label}</Button>}
    </Stack>
  } else if (schema.kind === 'boolean' || schema.kind === 'enum') {
    control = <TextField select disabled={disabled} label={label} value={value === undefined || value === null ? '' : String(value)} onBlur={onBlur}
      onChange={(event) => onChange(schema.kind === 'boolean' ? event.target.value === 'true' : event.target.value)}>
      <MenuItem value="" disabled>Choose a value</MenuItem>
      {schema.kind === 'boolean' ? [<MenuItem key="true" value="true">Yes</MenuItem>, <MenuItem key="false" value="false">No</MenuItem>]
        : schema.kind === 'enum' ? schema.values.map((choice) => <MenuItem key={choice} value={choice}>{choice}</MenuItem>) : null}
    </TextField>
  } else {
    const numeric = schema.kind === 'integer' || schema.kind === 'number'
    control = <TextField disabled={disabled} label={label} inputProps={{ inputMode: numeric ? 'decimal' : 'text' }} value={typeof value === 'string' || typeof value === 'number' ? value : ''}
      onBlur={onBlur} onChange={(event) => {
        const raw = event.target.value
        const number = Number(raw)
        const complete = /^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw)
        const safe = numeric && complete && Number.isFinite(number)
          && (schema.kind !== 'integer' || Number.isSafeInteger(number))
          && decimalIdentity(raw) === decimalIdentity(String(number))
        onChange(numeric && safe ? number : raw)
      }} />
  }
  return <Stack spacing={1}>
    {nullable && <FormControlLabel label={`${label}: explicit unknown (null)`} control={<Checkbox disabled={disabled} checked={value === null}
      onChange={(_, checked) => onChange(checked ? null : undefined)} />} />}
    {(value !== null || !nullable) && control}
  </Stack>
}
