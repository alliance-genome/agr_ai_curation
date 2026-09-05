import { useProfileValidatorCatalog } from './ProfileValidatorCatalog'
import { canonicalAuthoringJson } from '../authoringContext'
import { useState } from 'react'
import { Box, Button, IconButton, Popover, Stack, Typography } from '@mui/material'
import HelpOutline from '@mui/icons-material/HelpOutline'
import type { GenericProfileContract, ProfileMappingDiagnostic } from '@/services/genericProfileService'
import { profileFieldRows, type ProfileFieldAddress } from './profileEditorModel'
import { friendlyValidatorName, mappingUsesField, profileFieldPath } from './profileMappingUi'

export function ValidatorAttachmentHeading() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  return <Stack direction="row" alignItems="center"><span>Validator attached</span><IconButton aria-label="About validator attachments" onClick={e => setAnchor(e.currentTarget)}><HelpOutline fontSize="small" /></IconButton><Popover open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}><Box sx={{ p: 2, maxWidth: 350 }}><Typography>An attached validator is configured for this detail. This does not mean an answer has passed validation. Parts can have different validators.</Typography><Button onClick={() => setAnchor(null)}>Close</Button></Box></Popover></Stack>
}
export default function ValidatorAttachmentStatus({ value, address, issues = [], onEdit }: { value: GenericProfileContract; address: ProfileFieldAddress; issues?: ProfileMappingDiagnostic[]; onEdit?: () => void }) {
  const { capabilities: catalog, loaded, error } = useProfileValidatorCatalog()
  const path = profileFieldPath(value, address)
  const mappings = value.validator_mappings ?? []
  const own = mappings.filter(mapping => mappingUsesField(mapping, path))
  const parts = profileFieldRows(value).filter(row => row.address.length > address.length && row.address.slice(0, address.length).join('.') === address.join('.') && mappings.some(mapping => mappingUsesField(mapping, profileFieldPath(value, row.address)))).length
  return <Stack alignItems="flex-start" spacing={0.5}>
    {!own.length && <Typography>No</Typography>}
    {own.map(mapping => {
      const prefix = `validator_mappings[${mappings.indexOf(mapping)}]`
      const cap = catalog.find(c => canonicalAuthoringJson(c.capability_ref) === canonicalAuthoringJson(mapping.capability_ref))
      const attention = (loaded && !cap) || (cap && (!cap.selectable || cap.fingerprint !== mapping.capability_fingerprint)) || issues.some(issue => issue.path === prefix || issue.path.startsWith(prefix + '.'))
      const label = `${attention ? 'Needs attention' : error ? 'Status unavailable' : 'Yes'} · ${cap?.metadata.display_name || friendlyValidatorName(mapping.capability_ref.binding_id)}`
      return onEdit ? <Button key={mapping.mapping_id} onClick={onEdit} sx={{ textTransform: 'none', textAlign: 'left', p: 0, minHeight: 44 }}>{label}</Button> : <Typography key={mapping.mapping_id}>{label}</Typography>
    })}
    {parts > 0 && <Typography variant="body2" color="text.secondary">{parts} {parts === 1 ? 'part has a validator' : 'parts have validators'}</Typography>}
  </Stack>
}
