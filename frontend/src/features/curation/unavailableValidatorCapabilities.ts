export interface UnavailableValidatorCapability {
  validator_binding_id?: string
  label?: string
  state?: string
  state_explanation?: string
  affected_fields?: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function requiredMetadataString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

export function unavailableValidatorCapabilities(value: unknown): UnavailableValidatorCapability[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.filter(isRecord).map((entry) => ({
    validator_binding_id: optionalString(entry.validator_binding_id),
    label: optionalString(entry.label),
    state: optionalString(entry.state),
    state_explanation: optionalString(entry.state_explanation),
    affected_fields: Array.isArray(entry.affected_fields)
      ? entry.affected_fields.filter((field): field is string => typeof field === 'string')
      : undefined,
  }))
}

export function unavailableCapabilityMessage(
  capability: UnavailableValidatorCapability,
): string {
  const label = requiredMetadataString(capability.label)
  const explanation = requiredMetadataString(capability.state_explanation)
  const missingFields = [
    label ? null : 'label',
    explanation ? null : 'state_explanation',
  ].filter((field): field is string => field !== null)

  if (missingFields.length > 0) {
    const bindingId = requiredMetadataString(capability.validator_binding_id)
    const bindingContext = bindingId ? ` for ${bindingId}` : ''
    return `Unavailable validator capability metadata${bindingContext} is incomplete: missing ${missingFields.join(' and ')}.`
  }

  return `${label}: ${explanation}`
}
