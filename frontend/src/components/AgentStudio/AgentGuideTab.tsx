/**
 * AgentGuideTab
 *
 * The curator guide for one agent, in this order: when to use it and when
 * not to (stripes), what it reads, capabilities, limitations, and tools.
 * Sections render only when the documentation payload carries their data.
 * Nothing is synthesized in the frontend.
 */

import { Box, Button, Typography } from '@mui/material'
import AutoAwesomeOutlinedIcon from '@mui/icons-material/AutoAwesomeOutlined'

import type { AgentCapability, AgentDocumentation, DataSourceInfo } from '@/types/promptExplorer'
import AgentToolsTable from './AgentToolsTable'
import { MONO_FONT_FAMILY, SectionHeading } from './agentGuidePrimitives'

interface AgentGuideTabProps {
  documentation?: AgentDocumentation
  tools: string[]
  toolDescriptions: Record<string, string>
  narrow?: boolean
  onShowToolDetails: (toolId: string) => void
  onDraftGuide: () => void
}

const sectionSx = { display: 'flex', flexDirection: 'column', gap: 0.75 } as const
const listSx = { m: 0, pl: 2.25, maxWidth: '70ch', fontSize: 13, '& li': { my: 0.25 } } as const

function Stripe({ tone, heading, items }: { tone: 'success' | 'warning'; heading: string; items: string[] }) {
  return (
    <Box
      component="section"
      aria-label={heading}
      sx={{ ...sectionSx, borderLeft: 3, borderColor: `${tone}.main`, pl: 1.5 }}
    >
      <SectionHeading>{heading}</SectionHeading>
      <Box component="ul" sx={listSx}>
        {items.map((item) => <li key={item}>{item}</li>)}
      </Box>
    </Box>
  )
}

function DataSourceRow({ source }: { source: DataSourceInfo }) {
  const details = [
    source.description,
    source.species_supported && source.species_supported.length > 0 ? `Species: ${source.species_supported.join(', ')}.` : null,
    source.data_types && source.data_types.length > 0 ? `Data types: ${source.data_types.join(', ')}.` : null,
  ].filter(Boolean).join(' ')
  return (
    <>
      <Box component="span" sx={{ fontWeight: 500 }}>{source.name}</Box>
      {details ? `: ${details}` : ''}
    </>
  )
}

function CapabilityRow({ capability }: { capability: AgentCapability }) {
  const hasExample = Boolean(capability.example_query || capability.example_result)
  return (
    <Box
      component="li"
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: '200px minmax(0, 1fr)' },
        gap: '4px 14px',
        py: 1,
        fontSize: 13,
        borderTop: 1,
        borderColor: 'divider',
        '&:last-of-type': { borderBottom: 1, borderColor: 'divider' },
      }}
    >
      <Box sx={{ fontWeight: 500 }}>{capability.name}</Box>
      <Box sx={{ color: 'text.secondary' }}>{capability.description}</Box>
      {hasExample && (
        <Box sx={{ gridColumn: { sm: 2 }, color: 'text.secondary', fontSize: 12.5 }}>
          {capability.example_query && (
            <Box
              component="code"
              sx={{ fontFamily: MONO_FONT_FAMILY, backgroundColor: 'action.hover', px: 0.625, py: '1px', borderRadius: 0.5 }}
            >
              {capability.example_query}
            </Box>
          )}
          {capability.example_query && capability.example_result ? ' returns ' : null}
          {!capability.example_query && capability.example_result ? 'Returns ' : null}
          {capability.example_result}
        </Box>
      )}
    </Box>
  )
}

function AgentGuideTab({
  documentation,
  tools,
  toolDescriptions,
  narrow = false,
  onShowToolDetails,
  onDraftGuide,
}: AgentGuideTabProps) {
  const useWhen = documentation?.use_when ?? []
  const avoidWhen = documentation?.avoid_when ?? []
  const dataSources = documentation?.data_sources ?? []
  const capabilities = documentation?.capabilities ?? []
  const limitations = documentation?.limitations ?? []
  const hasGuide = useWhen.length > 0 || avoidWhen.length > 0 || dataSources.length > 0
    || capabilities.length > 0 || limitations.length > 0

  const toolsSection = (
    <AgentToolsTable tools={tools} descriptions={toolDescriptions} onShowDetails={onShowToolDetails} />
  )

  if (!hasGuide) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.25 }}>
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 0.75,
            textAlign: 'center',
            color: 'text.secondary',
            p: 3,
            border: 1,
            borderStyle: 'dashed',
            borderColor: 'divider',
            borderRadius: 2,
            fontSize: 13,
          }}
        >
          <Typography component="h3" sx={{ fontSize: 13, fontWeight: 500, color: 'text.primary' }}>
            No curator guide yet
          </Typography>
          <Typography variant="body2" sx={{ maxWidth: '60ch' }}>
            This agent has no capabilities, limitations, or usage guidance written. Its prompts are on the Prompts tab.
          </Typography>
          <Button
            size="small"
            variant="outlined"
            startIcon={<AutoAwesomeOutlinedIcon />}
            onClick={onDraftGuide}
            sx={{ mt: 1, textTransform: 'none' }}
          >
            Ask Claude to draft a guide
          </Button>
        </Box>
        {toolsSection}
      </Box>
    )
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.25 }}>
      {(useWhen.length > 0 || avoidWhen.length > 0) && (
        <Box sx={{ display: 'grid', gridTemplateColumns: narrow ? '1fr' : 'repeat(2, minmax(0, 1fr))', gap: 2.25 }}>
          {useWhen.length > 0 && <Stripe tone="success" heading="When to use it" items={useWhen} />}
          {avoidWhen.length > 0 && <Stripe tone="warning" heading="When not to use it" items={avoidWhen} />}
        </Box>
      )}

      {dataSources.length > 0 && (
        <Box component="section" aria-label="What it needs and returns" sx={sectionSx}>
          <SectionHeading>What it needs and returns</SectionHeading>
          <Box
            component="dl"
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: '150px minmax(0, 1fr)' },
              gap: '4px 14px',
              m: 0,
              fontSize: 13,
              maxWidth: '78ch',
            }}
          >
            <Box component="dt" sx={{ color: 'text.secondary', m: 0 }}>Reads</Box>
            <Box component="dd" sx={{ m: 0 }}>
              <Box component="ul" sx={{ m: 0, pl: 0, listStyle: 'none', '& li': { my: 0.25 } }}>
                {dataSources.map((source) => (
                  <li key={source.name}><DataSourceRow source={source} /></li>
                ))}
              </Box>
            </Box>
          </Box>
        </Box>
      )}

      {capabilities.length > 0 && (
        <Box component="section" aria-label="Capabilities" sx={sectionSx}>
          <SectionHeading>Capabilities</SectionHeading>
          <Box component="ul" sx={{ listStyle: 'none', m: 0, p: 0 }}>
            {capabilities.map((capability) => (
              <CapabilityRow key={capability.name} capability={capability} />
            ))}
          </Box>
        </Box>
      )}

      {limitations.length > 0 && (
        <Box component="section" aria-label="Limitations" sx={sectionSx}>
          <SectionHeading>Limitations</SectionHeading>
          <Box component="ul" sx={{ ...listSx, '& li::marker': { color: 'warning.main' } }}>
            {limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
          </Box>
        </Box>
      )}

      {toolsSection}
    </Box>
  )
}

export default AgentGuideTab
