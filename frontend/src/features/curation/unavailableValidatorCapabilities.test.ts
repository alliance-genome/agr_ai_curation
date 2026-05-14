import { describe, expect, it } from 'vitest'

import {
  unavailableCapabilityMessage,
  unavailableValidatorCapabilities,
} from './unavailableValidatorCapabilities'

describe('unavailable validator capability metadata', () => {
  it('formats the backend-provided label and state explanation directly', () => {
    const [capability] = unavailableValidatorCapabilities([
      {
        validator_binding_id: 'fixture.lookup',
        label: 'Object lookup',
        state: 'under_development',
        state_explanation: 'Object lookup is being wired.',
      },
    ])

    expect(unavailableCapabilityMessage(capability!)).toBe(
      'Object lookup: Object lookup is being wired.',
    )
  })

  it('surfaces incomplete metadata without fabricating capability text', () => {
    const [capability] = unavailableValidatorCapabilities([
      {
        validator_binding_id: 'fixture.lookup',
        state: 'under_development',
      },
    ])

    expect(unavailableCapabilityMessage(capability!)).toBe(
      'Unavailable validator capability metadata for fixture.lookup is incomplete: missing label and state_explanation.',
    )
  })
})
