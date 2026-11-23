import { useState, type MouseEvent } from 'react'
import { Box, Button, Menu, MenuItem, Tab, Tabs } from '@mui/material'

import { VIEWER_FILE_PANEL_ID, viewerFileTabId } from './TextFileView'
import { fileDisplayLabel, isPdfFile, type ViewerFile } from './viewerFiles'

/** Above this many files the strip gains an "All files (N)" menu (Q15, 21 Nov 2026). */
export const FILES_MENU_THRESHOLD = 6

/** Approved by Chris (23 Nov 2026). */
const MAIN_PAPER_SUFFIX = '(main paper)'
const TEXT_MARKER = 'Text'

/** Starts with the visible label (name and "(main paper)"), so a voice command naming what is seen finds the tab. */
function fileAccessibleName(file: ViewerFile, position: number, total: number): string {
  const visible = file.role === 'main' ? `${fileDisplayLabel(file)} ${MAIN_PAPER_SUFFIX}` : fileDisplayLabel(file)
  const base = `${visible}, file ${position} of ${total}`
  return isPdfFile(file) ? base : `${base}, converted text`
}

function FileLabel({ file }: { file: ViewerFile }) {
  return (
    <Box
      component="span"
      sx={{ display: 'inline-flex', alignItems: 'baseline', gap: 0.5, minWidth: 0, textTransform: 'none' }}
    >
      <Box
        component="span"
        title={fileDisplayLabel(file)}
        sx={{ maxWidth: '24ch', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
      >
        {fileDisplayLabel(file)}
      </Box>
      {file.role === 'main' ? <Box component="span">{MAIN_PAPER_SUFFIX}</Box> : null}
      {!isPdfFile(file) ? (
        <Box
          component="span"
          sx={{ fontSize: '0.7rem', fontWeight: 700, border: 1, borderRadius: 0.5, px: 0.5 }}
        >
          {TEXT_MARKER}
        </Box>
      ) : null}
    </Box>
  )
}

interface ViewerFileTabsProps {
  files: ViewerFile[]
  activeFileId: string
  onSelectFile: (fileId: string) => void
}

/** One tab per file of the paper; the files menu above six files. */
export function ViewerFileTabs({ files, activeFileId, onSelectFile }: ViewerFileTabsProps) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const showMenu = files.length > FILES_MENU_THRESHOLD

  const openMenu = (event: MouseEvent<HTMLElement>) => setMenuAnchor(event.currentTarget)
  const closeMenu = () => setMenuAnchor(null)

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', minWidth: 0, gap: 0.5 }}>
      {showMenu ? (
        <>
          <Button
            size="small"
            variant="outlined"
            aria-haspopup="menu"
            aria-expanded={menuAnchor ? 'true' : undefined}
            aria-controls={menuAnchor ? 'viewer-files-menu' : undefined}
            aria-label={`Show all ${files.length} files of this paper`}
            onClick={openMenu}
            sx={{ flex: '0 0 auto', textTransform: 'none', whiteSpace: 'nowrap' }}
          >
            {`All files (${files.length})`}
          </Button>
          <Menu id="viewer-files-menu" anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={closeMenu}>
            {files.map((file, index) => (
              <MenuItem
                key={file.file_id}
                selected={file.file_id === activeFileId}
                aria-label={fileAccessibleName(file, index + 1, files.length)}
                onClick={() => {
                  closeMenu()
                  onSelectFile(file.file_id)
                }}
              >
                <FileLabel file={file} />
              </MenuItem>
            ))}
          </Menu>
        </>
      ) : null}
      <Tabs
        value={activeFileId}
        onChange={(_event, fileId: string) => onSelectFile(fileId)}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        aria-label="Files of this paper"
        sx={{ minHeight: 36, minWidth: 0, flex: '1 1 auto' }}
      >
        {files.map((file, index) => (
          <Tab
            key={file.file_id}
            value={file.file_id}
            id={viewerFileTabId(file.file_id)}
            aria-controls={VIEWER_FILE_PANEL_ID}
            aria-label={fileAccessibleName(file, index + 1, files.length)}
            label={<FileLabel file={file} />}
            sx={{ minHeight: 36, py: 0.5, px: 1.25, fontWeight: file.file_id === activeFileId ? 700 : 400 }}
          />
        ))}
      </Tabs>
    </Box>
  )
}
