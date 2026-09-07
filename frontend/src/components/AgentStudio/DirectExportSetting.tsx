import { Alert, Box, FormControlLabel, Switch, Typography } from '@mui/material'

export default function DirectExportSetting({ value, onChange, isDefault = false }: {
  value: 'ai' | 'direct'
  onChange: (value: 'ai' | 'direct') => void
  isDefault?: boolean
}) {
  return <Box sx={{ display: 'grid', gap: 1 }}>
    <FormControlLabel control={<Switch inputProps={{ role: 'switch' }} checked={value === 'direct'}
      onChange={(_, checked) => onChange(checked ? 'direct' : 'ai')} />}
      label={isDefault ? 'Use direct export for new flow steps' : 'Export structured data directly — faster'} />
    <Typography variant="body2">Copy selected fields from the connected extractors into CSV, TSV or JSON without another AI call. You can rename and reorder columns; values are not rewritten or combined.</Typography>
    {value === 'direct' && <Alert severity="info">
      {isDefault ? 'New flow steps will start in direct export mode. Choose their source fields in the flow. Existing steps keep their settings. ' : ''}
      Agent prompts, group prompts and output instructions do not run in direct export mode. Their text is kept for AI output mode.
      Answers with parts and lists stay together: JSON keeps their structure; CSV and TSV store them as JSON inside a cell. Separate source records keep separate rows.
    </Alert>}
  </Box>
}
