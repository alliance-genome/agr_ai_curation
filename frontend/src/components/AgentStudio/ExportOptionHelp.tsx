import { useId, useState, type ReactNode } from 'react'
import { Box, Button, IconButton, Popover, Typography } from '@mui/material'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'

export default function ExportOptionHelp({ title, children }: { title: string; children: ReactNode }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const titleId = useId()
  return <>
    <IconButton aria-label={`About ${title.toLowerCase()}`} aria-haspopup="dialog"
      aria-expanded={Boolean(anchor)} onClick={(event) => setAnchor(event.currentTarget)}
      sx={{ color: 'text.secondary', flexShrink: 0 }}>
      <InfoOutlinedIcon fontSize="small" />
    </IconButton>
    <Popover open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      transformOrigin={{ vertical: 'top', horizontal: 'right' }} role="dialog" aria-labelledby={titleId}
      slotProps={{ paper: { sx: { width: 380, maxWidth: 'calc(100vw - 32px)', p: 2 } } }}>
      <Typography id={titleId} component="h3" variant="subtitle2" sx={{ mb: 1 }}>{title}</Typography>
      <Box sx={{ display: 'grid', gap: 1, '& p': { typography: 'body2', m: 0 } }}>{children}</Box>
      <Button onClick={() => setAnchor(null)} sx={{ mt: 1 }}>Close</Button>
    </Popover>
  </>
}
