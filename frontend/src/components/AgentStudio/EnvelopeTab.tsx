/**
 * EnvelopeTab
 *
 * The Agent Browser's Envelope tab: one sentence on what the agent produces,
 * a status line with counts, an object picker, one field table for the
 * selected object, the validators on that object, and a closed provenance
 * disclosure.
 */

import { useEffect, useId, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Box, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material'

import type { DomainEnvelopeMetadata } from '@/services/agentStudioService'
import type { AgentBrowserFocus } from './agentBrowserRequest'
import {
  createProviderWordResolver,
  envelopeCounts,
  envelopeObjectChoices,
  groupObjectFields,
  objectValidators,
} from './envelopePresentation'
import type { EnvelopeFieldGroupView, EnvelopeValidatorView } from './envelopePresentation'
import EnvelopeFieldTable from './EnvelopeFieldTable'
import EnvelopeValidatorList from './EnvelopeValidatorList'
import EnvelopeProvenance from './EnvelopeProvenance'
import { MONO_FONT_FAMILY, SectionHeading, StateDot } from './agentGuidePrimitives'

interface EnvelopeTabProps {
  metadata: DomainEnvelopeMetadata
  narrow?: boolean
  /** Selects the object that holds this field and highlights the field row. */
  focus?: AgentBrowserFocus | null
}

function Stat({ children }: { children: ReactNode }) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.625 }}>
      {children}
    </Box>
  )
}

function Strong({ children }: { children: ReactNode }) {
  return <Box component="b" sx={{ fontWeight: 500, color: 'text.primary' }}>{children}</Box>
}

function EnvelopeTab({ metadata, narrow = false, focus = null }: EnvelopeTabProps) {
  const choices = useMemo(() => envelopeObjectChoices(metadata), [metadata])
  const [selectedChoiceId, setSelectedChoiceId] = useState<string | null>(choices[0]?.id ?? null)
  const validatorsHeadingId = useId()
  const providerWord = useMemo(() => createProviderWordResolver(metadata.schema_refs), [metadata.schema_refs])

  useEffect(() => {
    if (!choices.some((choice) => choice.id === selectedChoiceId)) {
      setSelectedChoiceId(choices[0]?.id ?? null)
    }
  }, [choices, selectedChoiceId])

  // A focus request picks the object choice that holds the focused object.
  useEffect(() => {
    if (!focus) return
    const choice = choices.find((candidate) => candidate.objects.some((object) => object.object_type === focus.objectType))
    if (choice) setSelectedChoiceId(choice.id)
  }, [focus, choices])

  const selectedChoice = choices.find((choice) => choice.id === selectedChoiceId) ?? choices[0]
  const counts = envelopeCounts(metadata)
  const primaryObject = choices[0]?.objects[0]

  const fieldGroups: EnvelopeFieldGroupView[] = useMemo(() => {
    if (!selectedChoice) return []
    if (selectedChoice.objects.length === 1) {
      return groupObjectFields(selectedChoice.objects[0])
    }
    // Embedded references: one group per object, each labeled with the object name.
    return selectedChoice.objects.map((object) => ({
      id: object.object_type,
      label: object.display_name,
      fields: object.fields,
    }))
  }, [selectedChoice])

  const validators: EnvelopeValidatorView[] = useMemo(() => {
    if (!selectedChoice) return []
    const seen = new Set<string>()
    return selectedChoice.objects.flatMap((object) => objectValidators(object)).filter((validator) => {
      if (seen.has(validator.validatorId)) return false
      seen.add(validator.validatorId)
      return true
    })
  }, [selectedChoice])

  if (!selectedChoice || !primaryObject) {
    return (
      <Typography variant="body2" color="text.secondary">
        This domain pack declares no envelope objects.
      </Typography>
    )
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.25 }}>
      <Typography variant="body2" sx={{ fontSize: 13, color: 'text.secondary', maxWidth: '78ch' }}>
        Produces <Strong>{primaryObject.display_name}</Strong> objects
        {primaryObject.description ? `. ${primaryObject.description}` : '.'}
      </Typography>

      <Box
        component="p"
        sx={{ m: 0, display: 'flex', alignItems: 'center', gap: 1.75, flexWrap: 'wrap', fontSize: 13, color: 'text.secondary' }}
      >
        <Stat>
          <StateDot tone="active" />
          <Strong>{counts.activeValidators}</Strong>
          {' '}
          {counts.activeValidators === 1 ? 'validator' : 'validators'} active
        </Stat>
        {counts.underDevelopmentValidators > 0 && (
          <Stat>
            <StateDot tone="under_development" />
            <Strong>{counts.underDevelopmentValidators}</Strong>
            {' under development'}
          </Stat>
        )}
        <Stat>
          <Strong>{counts.requiredFields}</Strong>
          {` required ${counts.requiredFields === 1 ? 'field' : 'fields'}`}
        </Stat>
        <Stat>
          <Strong>{counts.blockingChecks}</Strong>
          {` blocking ${counts.blockingChecks === 1 ? 'check' : 'checks'}`}
        </Stat>
        <Stat>
          {'Pack '}
          <Strong><Box component="span" sx={{ fontFamily: MONO_FONT_FAMILY }}>{metadata.domain_pack_id}</Box></Strong>
          {` v${metadata.domain_pack_version}`}
        </Stat>
      </Box>

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        <SectionHeading>Object</SectionHeading>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={selectedChoice.id}
          onChange={(_event, nextValue: string | null) => {
            if (nextValue) setSelectedChoiceId(nextValue)
          }}
          aria-label="Envelope object"
          sx={{ flexWrap: 'wrap', '& .MuiToggleButton-root': { textTransform: 'none', fontSize: 12.5, py: 0.5, px: 1.25 } }}
        >
          {choices.map((choice) => (
            <ToggleButton key={choice.id} value={choice.id}>
              {choice.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Box>

      <EnvelopeFieldTable
        groups={fieldGroups}
        ariaLabel={`${selectedChoice.label} fields`}
        providerWord={providerWord}
        narrow={narrow}
        highlightFieldPath={focus?.fieldPath}
      />

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        <SectionHeading id={validatorsHeadingId}>Validators on this object</SectionHeading>
        <EnvelopeValidatorList validators={validators} ariaLabelledBy={validatorsHeadingId} />
      </Box>

      <EnvelopeProvenance metadata={metadata} objects={selectedChoice.objects} />
    </Box>
  )
}

export default EnvelopeTab
