/**
 * EnvelopeProvenance
 *
 * Closed-by-default disclosure with a definition list: domain pack id and
 * version, the schema each object comes from (with a link at the pinned
 * commit), provider reference rows, and the object's definition notes.
 */

import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { Box, Button, Collapse, Link, Typography } from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'

import type {
  DomainEnvelopeMetadata,
  DomainEnvelopeObjectMetadata,
  DomainEnvelopeSchemaRef,
} from '@/services/agentStudioService'
import { humanizeStatus, shortCommit, sourceOfTruthWord } from './envelopePresentation'
import { MONO_FONT_FAMILY } from './agentGuidePrimitives'

interface EnvelopeProvenanceProps {
  metadata: DomainEnvelopeMetadata
  objects: DomainEnvelopeObjectMetadata[]
}

interface ProvenanceRow {
  key: string
  term: string
  detail: ReactNode
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ')
}

function schemaFileUrl(schemaRef: DomainEnvelopeSchemaRef | undefined, sourceFile: string): string | null {
  if (!schemaRef?.uri) return null
  return `${schemaRef.uri.replace(/\/$/, '')}/${sourceFile.replace(/^\//, '')}`
}

/**
 * Turn one provider's reference record into rows. String values become one
 * row each, string lists join with commas, nested records are skipped.
 */
function providerRows(
  providerKey: string,
  value: unknown,
  schemaRefs: DomainEnvelopeSchemaRef[],
  rowPrefix: string
): ProvenanceRow[] {
  const providerWord = sourceOfTruthWord(providerKey)
  if (!isRecord(value)) {
    return [{ key: `${rowPrefix}:${providerKey}`, term: providerWord, detail: String(value) }]
  }

  const schemaRefId = typeof value.schema_ref === 'string' ? value.schema_ref : null
  const schemaRef = schemaRefId ? schemaRefs.find((ref) => ref.schema_id === schemaRefId) : undefined

  return Object.entries(value).flatMap(([entryKey, entryValue]): ProvenanceRow[] => {
    if (entryKey === 'schema_ref') return []
    const rowKey = `${rowPrefix}:${providerKey}:${entryKey}`
    const term = `${providerWord} ${humanizeKey(entryKey)}`

    if (entryKey === 'source_file' && typeof entryValue === 'string') {
      const url = schemaFileUrl(schemaRef, entryValue)
      return [{
        key: rowKey,
        term,
        detail: url ? (
          <Link href={url} target="_blank" rel="noreferrer" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
            <Box component="span" sx={{ fontFamily: MONO_FONT_FAMILY }}>{entryValue}</Box>
            <OpenInNewIcon sx={{ fontSize: 14 }} aria-label="opens in a new tab" />
          </Link>
        ) : (
          <Box component="span" sx={{ fontFamily: MONO_FONT_FAMILY }}>{entryValue}</Box>
        ),
      }]
    }
    if (entryKey === 'commit' && typeof entryValue === 'string') {
      return [{ key: rowKey, term, detail: <Box component="span" sx={{ fontFamily: MONO_FONT_FAMILY }}>{shortCommit(entryValue)}</Box> }]
    }
    if (typeof entryValue === 'string') {
      return [{ key: rowKey, term, detail: entryValue }]
    }
    if (Array.isArray(entryValue) && entryValue.every((item) => typeof item === 'string')) {
      return [{ key: rowKey, term, detail: (entryValue as string[]).join(', ') }]
    }
    return []
  })
}

function objectRows(object: DomainEnvelopeObjectMetadata, schemaRefs: DomainEnvelopeSchemaRef[]): ProvenanceRow[] {
  const rows: ProvenanceRow[] = []
  if (object.schema_ref) {
    const ref = object.schema_ref
    const label = [ref.name || ref.schema_id, ref.version ? shortCommit(ref.version) : null].filter(Boolean).join(' at ')
    rows.push({
      key: `${object.object_type}:schema`,
      term: 'Schema',
      detail: ref.uri ? <Link href={ref.uri} target="_blank" rel="noreferrer">{label}</Link> : label,
    })
  }
  Object.entries(object.provider_refs).forEach(([providerKey, value]) => {
    rows.push(...providerRows(providerKey, value, schemaRefs, object.object_type))
  })
  if (object.definition_notes.length > 0) {
    rows.push({
      key: `${object.object_type}:notes`,
      term: 'Notes',
      detail: (
        <Box component="ul" sx={{ m: 0, pl: 2 }}>
          {object.definition_notes.map((note) => <li key={note}>{note}</li>)}
        </Box>
      ),
    })
  }
  return rows
}

function DefinitionList({ rows }: { rows: ProvenanceRow[] }) {
  return (
    <Box
      component="dl"
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: '150px minmax(0, 1fr)' },
        gap: '3px 14px',
        m: 0,
        fontSize: 12.5,
      }}
    >
      {rows.map((row) => (
        <Box key={row.key} sx={{ display: 'contents' }}>
          <Box component="dt" sx={{ color: 'text.secondary', m: 0 }}>{row.term}</Box>
          <Box component="dd" sx={{ m: 0, minWidth: 0, overflowWrap: 'anywhere' }}>{row.detail}</Box>
        </Box>
      ))}
    </Box>
  )
}

function EnvelopeProvenance({ metadata, objects }: EnvelopeProvenanceProps) {
  const [open, setOpen] = useState(false)
  const regionId = useId()

  const packRows: ProvenanceRow[] = [
    {
      key: 'pack',
      term: 'Domain pack',
      detail: (
        <>
          <Box component="span" sx={{ fontFamily: MONO_FONT_FAMILY }}>{metadata.domain_pack_id}</Box>
          {` v${metadata.domain_pack_version}, ${humanizeStatus(metadata.status)}`}
        </>
      ),
    },
    ...metadata.schema_refs.map((ref) => ({
      key: `pack-schema:${ref.schema_id}`,
      term: `${sourceOfTruthWord(ref.provider)} schema`,
      detail: (
        <>
          {ref.uri ? <Link href={ref.uri} target="_blank" rel="noreferrer">{ref.name || ref.schema_id}</Link> : (ref.name || ref.schema_id)}
          {ref.version ? (
            <>
              {' at '}
              <Box component="span" sx={{ fontFamily: MONO_FONT_FAMILY }}>{shortCommit(ref.version)}</Box>
            </>
          ) : null}
        </>
      ),
    })),
  ]

  return (
    <Box>
      <Button
        size="small"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={regionId}
        startIcon={(
          <ExpandMoreIcon
            sx={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 120ms' }}
          />
        )}
        sx={{ textTransform: 'none', fontWeight: 500, fontSize: 13, px: 0.5, minWidth: 0 }}
      >
        Schema and provenance
      </Button>
      <Collapse in={open} unmountOnExit>
        <Box id={regionId} sx={{ pt: 1, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
          <DefinitionList rows={packRows} />
          {objects.map((object) => {
            const rows = objectRows(object, metadata.schema_refs)
            if (rows.length === 0) return null
            return (
              <Box key={object.object_type}>
                {objects.length > 1 && (
                  <Typography component="h4" sx={{ fontSize: 12.5, fontWeight: 500, mb: 0.5 }}>
                    {object.display_name}
                  </Typography>
                )}
                <DefinitionList rows={rows} />
              </Box>
            )
          })}
        </Box>
      </Collapse>
    </Box>
  )
}

export default EnvelopeProvenance
