import { memo } from 'react'
import { Box } from '@mui/material'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** Render assistant prose safely; raw HTML and remote images are not chat content. */
export const ChatMarkdown = memo(function ChatMarkdown({ children }: { children: string }) {
  return <Box sx={{ typography: 'body2', whiteSpace: 'normal', overflowWrap: 'anywhere',
    '& p': { my: 1, whiteSpace: 'pre-wrap' }, '& > :first-child': { mt: 0 }, '& > :last-child': { mb: 0 },
    '& h1, & h2, & h3, & h4, & h5, & h6': { fontSize: '1em', fontWeight: 700, mt: 2, mb: 1 },
    '& ul, & ol': { pl: 3, my: 1 }, '& li': { my: 0.5 },
    '& pre': { overflowX: 'auto', p: 1, bgcolor: 'action.hover', borderRadius: 1 },
    '& code': { fontSize: '0.9em' }, '& a': { color: 'primary.main' },
    '& th, & td': { border: 1, borderColor: 'divider', p: 1, textAlign: 'left' },
    '& th': { bgcolor: 'action.hover' }, '& table': { borderCollapse: 'collapse' },
  }}>
    <Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']}
      components={{ table: ({ children }) => <Box sx={{ overflowX: 'auto' }}><table>{children}</table></Box> }}>
      {children}
    </Markdown>
  </Box>
})
