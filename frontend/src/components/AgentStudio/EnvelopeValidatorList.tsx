/**
 * EnvelopeValidatorList
 *
 * Lists each validator that touches the selected object once: state dot,
 * label, one-line description with its policy in words, and the number of
 * fields it covers.
 */

import { Box, Typography } from '@mui/material'

import type { EnvelopeValidatorView } from './envelopePresentation'
import { StateDot } from './agentGuidePrimitives'

interface EnvelopeValidatorListProps {
  validators: EnvelopeValidatorView[]
  ariaLabelledBy: string
}

function coverageLabel(validator: EnvelopeValidatorView): string {
  if (validator.fieldCount === 0) {
    return validator.coversWholeObject ? 'Whole object' : 'No fields'
  }
  const fields = `${validator.fieldCount} field${validator.fieldCount === 1 ? '' : 's'}`
  return validator.coversWholeObject ? `Whole object, ${fields}` : fields
}

function EnvelopeValidatorList({ validators, ariaLabelledBy }: EnvelopeValidatorListProps) {
  if (validators.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No automatic checks run on this object.
      </Typography>
    )
  }

  return (
    <Box
      component="ul"
      aria-labelledby={ariaLabelledBy}
      sx={{ listStyle: 'none', m: 0, p: 0, border: 1, borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}
    >
      {validators.map((validator) => (
        <Box
          component="li"
          key={validator.validatorId}
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '20px minmax(0, 1fr) auto', md: '20px minmax(160px, 230px) minmax(0, 1fr) auto' },
            gap: 1.25,
            px: 1.5,
            py: 0.875,
            alignItems: 'center',
            fontSize: 13,
            borderBottom: 1,
            borderColor: 'divider',
            '&:last-of-type': { borderBottom: 0 },
          }}
        >
          <StateDot tone={validator.state} />
          <Box sx={{ fontWeight: 500, minWidth: 0 }}>{validator.label}</Box>
          <Box sx={{ color: 'text.secondary', fontSize: 12.5, gridColumn: { xs: '2 / span 2', md: 'auto' } }}>
            {validator.description ? `${validator.description} ` : ''}
            {validator.policySentence}
            {validator.state === 'under_development' && (
              <Box component="span" sx={{ color: 'warning.main' }}>
                {' '}
                {validator.stateExplanation || 'Under development: not yet runnable.'}
              </Box>
            )}
          </Box>
          <Box sx={{ fontSize: 12, color: 'text.secondary', textAlign: 'right', whiteSpace: 'nowrap', gridRow: { xs: 1, md: 'auto' }, gridColumn: { xs: 3, md: 'auto' } }}>
            {coverageLabel(validator)}
          </Box>
        </Box>
      ))}
    </Box>
  )
}

export default EnvelopeValidatorList
