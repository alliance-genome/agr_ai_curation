import { useEffect, useMemo, useState } from 'react'
import { Alert, AlertTitle, Box, Button, Stack, Typography } from '@mui/material'
import type { AgentMetadata } from '@/services/agentStudioService'
import type { FlowDefinition } from './types'
import { extractionValidationNotices, noticeStorageKey } from './extractionValidationNotices'

interface Props {
  definition: FlowDefinition
  metadata: Record<string, AgentMetadata>
  ownerId?: string
  validatorSchemaKeys?: readonly string[]
  scopeId: string
}

function readDismissals(key: string | null): Record<string, string> {
  if (!key) return {}
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(Object.entries(value).filter(([, configuration]) => typeof configuration === 'string'))
  } catch { return {} }
}

/** Keyed by user/flow by the parent; dismissal is a browser preference, never consent. */
export default function ExtractionValidationNotices({ definition, metadata, ownerId, scopeId, validatorSchemaKeys }: Props) {
  const storageKey = noticeStorageKey(ownerId, scopeId)
  const [dismissed, setDismissed] = useState(() => readDismissals(storageKey))
  const [help, setHelp] = useState<string | null>(null)
  const notices = useMemo(() => extractionValidationNotices(definition, metadata, validatorSchemaKeys), [definition, metadata, validatorSchemaKeys])
  useEffect(() => {
    // Forget removed nodes and changed setups, including a validator added and later removed.
    setDismissed(previous => {
      const next = Object.fromEntries(Object.entries(previous).filter(([id, configuration]) =>
        notices.some(notice => notice.nodeId === id && notice.configuration === configuration)))
      return Object.keys(next).length === Object.keys(previous).length ? previous : next
    })
  }, [notices])
  useEffect(() => {
    if (storageKey) {
      try { localStorage.setItem(storageKey, JSON.stringify(dismissed)) } catch { /* Still dismiss in memory. */ }
    }
  }, [storageKey, dismissed])
  const visible = notices.filter(notice => !notice.hasValidator && dismissed[notice.nodeId] !== notice.configuration)
  if (!visible.length) return null
  return <Box component="section" aria-label="Extraction steps without database validation"
    sx={{ position: 'absolute', bottom: 12, left: 56, right: 12, maxWidth: 380, maxHeight: '40%', overflowY: 'auto', zIndex: 5 }}>
    <Stack spacing={1}>
      {visible.map(notice => <Alert key={notice.nodeId} severity="warning" role="status">
        <AlertTitle>{notice.label}: no database validation configured</AlertTitle>
        <Typography variant="body2">Extracted values will remain unverified against the database. You can keep editing, save, or run this flow.</Typography>
        <Stack direction="row" sx={{ flexWrap: 'wrap' }}>
          <Button size="small" aria-label={`Validation help for ${notice.label}`} aria-expanded={help === notice.nodeId}
            onClick={() => setHelp(help === notice.nodeId ? null : notice.nodeId)}>How to add validation</Button>
          <Button size="small" aria-label={`Dismiss validation notice for ${notice.label}`}
            onClick={() => setDismissed(previous => ({ ...previous, [notice.nodeId]: notice.configuration }))}>Dismiss</Button>
        </Stack>
        {help === notice.nodeId && <Typography variant="body2" sx={{ mt: 1 }}>
          Packaged output formats include automatic database checks where supported, even in custom agents.
          For a Custom Output Structure, open the agent in Workshop, edit its details to collect, and add a built-in or custom validator under Validation.
          For packaged formats, a compatible custom validator can replace an automatic check through a validation connection in this flow.
          Ask AI Chat for help choosing and connecting validators. Adding a check does not mean every field will be validated.
        </Typography>}
      </Alert>)}
    </Stack>
  </Box>
}
