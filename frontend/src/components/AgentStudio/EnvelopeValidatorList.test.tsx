import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import EnvelopeValidatorList from './EnvelopeValidatorList'
import type { EnvelopeValidatorView } from './envelopePresentation'

function validator(overrides: Partial<EnvelopeValidatorView>): EnvelopeValidatorView {
  return {
    validatorId: 'lookup',
    label: 'Term lookup',
    state: 'active',
    description: 'Resolves the term against the ontology.',
    policySentence: 'Blocking: a row cannot be submitted until this check passes.',
    fieldCount: 2,
    coversWholeObject: false,
    ...overrides,
  }
}

describe('EnvelopeValidatorList', () => {
  it('lists each validator once with its state, policy, and coverage', () => {
    render(
      <>
        <h3 id="validators-heading">Validators on this object</h3>
        <EnvelopeValidatorList
          ariaLabelledBy="validators-heading"
          validators={[
            validator({}),
            validator({
              validatorId: 'future',
              label: 'Reference materialization',
              state: 'under_development',
              stateExplanation: 'No durable reference identity exists at extraction time.',
              description: undefined,
              policySentence: 'Advisory: findings are shown but do not block submission.',
              fieldCount: 0,
              coversWholeObject: true,
            }),
            validator({ validatorId: 'single', label: 'Single field check', fieldCount: 1 }),
          ]}
        />
      </>
    )

    const list = screen.getByRole('list', { name: 'Validators on this object' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(3)

    expect(items[0]).toHaveTextContent('Term lookup')
    expect(items[0]).toHaveTextContent('Resolves the term against the ontology. Blocking: a row cannot be submitted until this check passes.')
    expect(items[0]).toHaveTextContent('2 fields')
    expect(within(items[0]).getByRole('img', { name: 'Active' })).toBeInTheDocument()

    expect(within(items[1]).getByRole('img', { name: 'Under development' })).toBeInTheDocument()
    expect(items[1]).toHaveTextContent('No durable reference identity exists at extraction time.')
    expect(items[1]).toHaveTextContent('Whole object')

    expect(items[2]).toHaveTextContent('1 field')
  })

  it('states when no validator touches the object', () => {
    render(<EnvelopeValidatorList validators={[]} ariaLabelledBy="x" />)
    expect(screen.getByText('No automatic checks run on this object.')).toBeInTheDocument()
  })
})
