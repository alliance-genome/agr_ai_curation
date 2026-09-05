import { useState, type ReactNode } from 'react'
import { Alert, Box, Button, Stack, TextField, Typography, useMediaQuery } from '@mui/material'
import { Panel, PanelGroup } from 'react-resizable-panels'
import { Root, PanelCard, ResizeHandle, TabBar, StyledTabs, StyledTab, TabContent } from '../components/AgentStudio/studioShellStyles'
import WorkshopHeader from '../components/AgentStudio/PromptWorkshop/WorkshopHeader'
import WorkshopNav from '../components/AgentStudio/PromptWorkshop/WorkshopNav'
import { NARROW_QUERY } from '../components/AgentStudio/PromptWorkshop/workshopStyles'
import ClaudeDrawer from '../components/AgentStudio/ClaudeDrawer'

/** Preview composes the production shell primitives, header, navigation and breakpoints. */
export default function StudioPreviewFrame({ children, name, chatOpen, onChatChange, onNew, onExample, dark, onAppearance }: {
  children: ReactNode; name: string; chatOpen: boolean; onChatChange: (open: boolean) => void
  onNew: () => void; onExample: () => void; dark: boolean; onAppearance: () => void
}) {
  const narrow = useMediaQuery('(max-width:1099px)')
  const fullDrawer = useMediaQuery('(max-width:719px)')
  const [notice, setNotice] = useState(false)
  const unavailable = () => setNotice(true)
  const chat = <Stack sx={{ height: '100%', p: 2 }} spacing={2}>
    <Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="h6">AI Chat</Typography><Button onClick={() => onChatChange(false)}>Hide</Button></Stack>
    <Typography color="text.secondary">Use this space to describe what you want to extract and refine your draft.</Typography>
    <Alert severity="info">AI is disconnected in this design preview.</Alert>
    <Box sx={{ flex: 1 }} />
    <TextField label="Ask AI Chat" placeholder="Help me collect stock names and suppliers…" multiline minRows={3} disabled />
  </Stack>
  return <Box sx={{ height: '100dvh', display: 'flex', flexDirection: 'column', bgcolor: 'background.default' }}>
    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 2, py: 1, borderBottom: 1, borderColor: 'divider' }}><Typography fontWeight={600}>AI Curation · Agent Studio</Typography><Button onClick={onAppearance}>{dark ? 'Light' : 'Dark'} appearance</Button></Stack>
    <Root sx={{ minHeight: 0 }}>
      <PanelGroup direction="horizontal" style={{ flex: 1, minWidth: 0, height: '100%', display: 'flex', overflow: 'hidden' }}>
        <Panel id="preview-work" order={1} defaultSize={70} minSize={50}>
          <PanelCard>
            <TabBar><StyledTabs value="agent_workshop" onChange={unavailable} aria-label="Agent Studio tabs"><StyledTab label="Agents" value="agents" /><StyledTab label="Flows" value="flows" /><StyledTab label="Agent Workshop" value="agent_workshop" /></StyledTabs>{(narrow || !chatOpen) && <Button onClick={() => onChatChange(true)}>AI Chat</Button>}</TabBar>
            <TabContent>
              <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden', containerType: 'inline-size', containerName: 'workshop' }}>
                <WorkshopHeader icon="" name={name ? `${name} extraction` : 'Custom extraction'} originLabel="Template: General PDF Extraction Agent" saveState="idle" lastSavedAt={null} dirty={Boolean(name)} canSave={false} canDelete={false} saving={false} onOpen={onExample} onNew={onNew} onSave={unavailable} onSaveAs={unavailable} onManage={unavailable} onDelete={unavailable} />
                <Box sx={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: '200px minmax(0, 1fr)', [NARROW_QUERY]: { gridTemplateColumns: 'minmax(0, 1fr)', gridTemplateRows: 'auto minmax(0, 1fr)' } }}>
                  <WorkshopNav section="output_structure" showOutputStructure onSectionChange={unavailable} dirty={{ setup: false, prompt: false, tools: false, outputStructure: Boolean(name), groups: [], any: Boolean(name) }} toolCount={0} versionCount={0} onAskClaude={() => onChatChange(true)} />
                  <Box sx={{ minWidth: 0, minHeight: 0, overflow: 'auto', px: 3, pt: 2.25, pb: 3.5, display: 'flex', flexDirection: 'column', gap: 2.5 }}>
                    {notice && <Alert severity="info" onClose={() => setNotice(false)}>This preview exercises Output Structure in the Studio layout. Other sections and saving are not connected.</Alert>}
                    {children}
                  </Box>
                </Box>
              </Box>
            </TabContent>
          </PanelCard>
        </Panel>
        {!narrow && chatOpen && <><ResizeHandle collapsed={false} /><Panel id="preview-chat" order={2} defaultSize={30} minSize={22} maxSize={50}><PanelCard>{chat}</PanelCard></Panel></>}
      </PanelGroup>
      {narrow && <ClaudeDrawer id="preview-chat-drawer" open={chatOpen} fullWidth={fullDrawer} onClose={() => onChatChange(false)}>{chat}</ClaudeDrawer>}
    </Root>
  </Box>
}
