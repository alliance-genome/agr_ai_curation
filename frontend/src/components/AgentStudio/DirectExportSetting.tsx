import { Alert, Box, FormControlLabel, Switch, Typography } from '@mui/material'
import ExportOptionHelp from './ExportOptionHelp'

export default function DirectExportSetting({ value, onChange, isDefault = false }: {
  value: 'ai' | 'direct'
  onChange: (value: 'ai' | 'direct') => void
  isDefault?: boolean
}) {
  return <Box sx={{ display: 'grid', gap: 1 }}>
    <Box sx={{ display: 'flex', alignItems: 'center' }}>
      <FormControlLabel sx={{ m: 0, gap: 0.75, '& .MuiFormControlLabel-label': { typography: 'body2' } }}
        control={<Switch size="small" inputProps={{ role: 'switch' }} checked={value === 'direct'}
          onChange={(_, checked) => onChange(checked ? 'direct' : 'ai')} />}
        label={isDefault ? 'Use direct export for new flow steps' : 'Export structured data directly — faster'} />
      <ExportOptionHelp title="Direct structured export">
        <p>The extractor agents decide which details to collect from the paper. This output step puts those details into a file.</p>
        <p>For example, if your stock extractor collects a stock name and supplier, direct export copies those answers into your CSV, TSV or JSON file. It is faster because AI does not need to arrange the output again.</p>
        <p>To change the details being collected, edit the connected extractor agent’s output structure. Then review the fields selected for this output step.</p>
        <p>Answers with several parts or lists stay together. JSON keeps their structure; CSV and TSV keep the parts together in one cell.</p>
      </ExportOptionHelp>
    </Box>
    <Typography variant="body2">Export the fields collected by the connected extractor agents, without asking AI to format them again.</Typography>
    {value === 'direct' && <Alert severity="info">
      {isDefault ? 'New flow steps will start in direct export mode. Existing steps keep their settings. ' : ''}
      {isDefault ? 'For each output step using direct export, ' : 'For this output step only, '}
      the agent prompt, group prompts and output instructions are not used. Their text stays saved for AI output mode. Extraction and validation steps still run as configured.
    </Alert>}
  </Box>
}
