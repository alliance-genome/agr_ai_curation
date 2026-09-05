import { createAppTheme } from './theme'
import StudioPreviewFrame from './design-preview/StudioPreviewFrame'
import { useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Box, Button, CssBaseline, Dialog, DialogContent, DialogTitle, Stack, ThemeProvider, Typography } from '@mui/material'
import OutputStructureWorkflow from './components/AgentStudio/PromptWorkshop/OutputStructureWorkflow'
import type { GenericProfileContract } from './services/genericProfileService'

const initial: GenericProfileContract = {
  name: 'Reagents',
  description: 'One reagent per paper, with the labels used by the authors and any stated source.',
  semantic_class: 'reagent_inventory_item',
  fields: [
    { key: 'paper_labels', display_name: 'Name used in the paper', description: 'Use the reagent name as written in the paper.', required: true, nullable: false, source_labels: ['synonym'], value_schema: { kind: 'string' } },
    { key: 'source_status', display_name: 'Made for this study or obtained elsewhere?', description: 'Whether the paper says the reagent was made for this study, came from elsewhere, or does not state a source.', required: true, nullable: false, value_schema: { kind: 'enum', values: ['new_in_paper', 'external', 'not_stated'] } },
    { key: 'sources', display_name: 'Supplier or provider', description: 'One supplier or provider explicitly stated in the paper, with its catalog or stock identifier. Do not invent a missing value.', required: false, nullable: true, value_schema: { kind: 'object', fields: [
      { key: 'name', display_name: 'Provider name', required: false, nullable: true, value_schema: { kind: 'string' } },
      { key: 'identifier', display_name: 'Catalog or stock identifier', required: false, nullable: true, value_schema: { kind: 'string' } },
    ] } },
  ], validator_mappings: [],
}
function Preview() {
  const [editorKey, setEditorKey] = useState(0)
  const [dark, setDark] = useState(false)
  const [value, setValue] = useState<GenericProfileContract>({ name: '', semantic_class: '', fields: [], validator_mappings: [] })
  const [switchExample, setSwitchExample] = useState<'blank' | 'reagents' | null>(null)
  const [chat, setChat] = useState(() => window.innerWidth >= 1100)
  const [checked, setChecked] = useState(false)
  const theme = useMemo(() => createAppTheme(dark ? 'dark' : 'light'), [dark])
  return <ThemeProvider theme={theme}><CssBaseline />
    <StudioPreviewFrame name={value.name} chatOpen={chat} onChatChange={setChat} onNew={() => setSwitchExample('blank')} onExample={() => setSwitchExample('reagents')} dark={dark} onAppearance={() => setDark(!dark)}>
      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} gap={1}>
        <Typography variant="body2" color="text.secondary">Design preview · edits are temporary</Typography>
        <Stack direction="row" gap={1}><Button sx={{ textTransform: 'none' }} onClick={() => setSwitchExample('blank')}>Start your own</Button><Button sx={{ textTransform: 'none' }} onClick={() => setSwitchExample('reagents')}>See reagent example</Button></Stack>
      </Stack>
      <Box sx={{ bgcolor: 'background.paper', py: 1 }}>
        <OutputStructureWorkflow key={editorKey} value={value} onChange={setValue} issues={[]} onValidate={() => setChecked(true)} onAskAI={() => setChat(true)} />
        {checked && <Typography role="status" color="text.secondary" sx={{ mt: 2 }}>This preview does not run server checks or save an agent. Field edits are available to try locally.</Typography>}
      </Box>

    </StudioPreviewFrame>
    <Dialog open={switchExample !== null} onClose={() => setSwitchExample(null)}><DialogTitle>Replace this preview draft?</DialogTitle><DialogContent><Typography>Your current preview edits will be replaced. Saved agents are unaffected.</Typography><Button onClick={() => setSwitchExample(null)}>Keep editing</Button><Button onClick={() => { setValue(switchExample === 'reagents' ? structuredClone(initial) : { name: '', semantic_class: '', fields: [], validator_mappings: [] }); setSwitchExample(null); setChecked(false); setEditorKey((key) => key + 1) }}>Replace preview draft</Button></DialogContent></Dialog>

  </ThemeProvider>
}
createRoot(document.getElementById('root')!).render(<Preview />)
