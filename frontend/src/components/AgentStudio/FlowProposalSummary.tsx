import { Box, Typography } from '@mui/material'
import type { FlowAuthoringProposal } from '@/types/promptExplorer'

const settingNames: Record<string, string> = {
  task_instructions: 'Initial instructions',
  step_goal: 'What this step should do',
  custom_instructions: 'Additional instructions',
  agent_id: 'Agent',
  agent_revision_id: 'Saved agent version',
  execution_receipt: 'Saved agent version',
  validation_attachments: 'Automatic validation',
  validation_groups: 'Automatic validation',
  include_evidence: 'Supporting evidence',
  output_filename_template: 'Download filename',
  projection_plan: 'Information in the download',
  output_key: 'Connection to other steps',
  agent_display_name: 'Step name',
  agent_description: 'Step description',
  prompt_version: 'Instructions version',
  position: 'Position on the canvas',
}

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const asList = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(asRecord) : []
const columnName = (column: Record<string, unknown>): string => String(column.header || column.key || 'Unnamed column')

function projectionDetails(path: string, before: unknown, after: unknown, plan: Record<string, unknown>): string[] {
  const property = path.split('projection_plan.')[1]?.split('.')[0]
  const previous = property ? { [property]: before } : asRecord(before)
  const next = property ? { [property]: after } : asRecord(after)
  if (!property && after == null) return ['Remove the custom download layout and use the default layout.']
  const details: string[] = []
  const columns = asList(plan.columns)
  const fieldName = (ref: unknown) => {
    const column = columns.find((item) => item.field_ref === ref)
    return column ? columnName(column) : 'an extracted detail (see Technical details for the field)'
  }
  const operators: Record<string, string> = {
    eq: 'equals', ne: 'does not equal', in: 'is one of', contains: 'contains',
    is_empty: 'is empty', is_not_empty: 'is not empty', gt: 'is greater than',
    gte: 'is at least', lt: 'is less than', lte: 'is at most',
  }
  for (const key of new Set([...Object.keys(previous), ...Object.keys(next)])) {
    const value = next[key]
    if (JSON.stringify(previous[key]) === JSON.stringify(value)) continue
    if (key === 'columns') {
      const oldColumns = asList(previous[key]); const newColumns = asList(value)
      const removed = oldColumns.filter((old) => !newColumns.some((item) => item.key === old.key))
      if (removed.length) details.push(`Remove download columns: ${removed.map(columnName).join(', ')}`)
      details.push(newColumns.length ? `Download columns, in order: ${newColumns.map(columnName).join(', ')}` : 'Use the default download columns.')
      for (const column of newColumns) {
        const old = oldColumns.find((item) => item.key === column.key)
        if (old && (JSON.stringify(old.field_ref) !== JSON.stringify(column.field_ref)
          || JSON.stringify(old.transform) !== JSON.stringify(column.transform))) {
          details.push(`Change how ${columnName(column)} is filled. Review its source and formatting in Technical details.`)
        }
      }
    } else if (key === 'filters') {
      const filters = asList(value)
      if (!filters.length) details.push('Remove row filters; include all matching records.')
      for (const filter of filters) {
        const operand = filter.op === 'in' ? filter.values : filter.value
        details.push(`Keep only rows where ${fieldName(filter.field_ref)} ${operators[String(filter.op)] || 'matches the configured rule'}${String(filter.op).startsWith('is_') ? '' : ` ${JSON.stringify(operand)}`}.`)
      }
    } else if (key === 'format') {
      details.push(`Output format: ${String(value || 'default').toUpperCase()}`)
    } else if (key === 'max_rows') {
      details.push(value == null ? 'Use the default row limit.' : `Limit the download to ${value} rows.`)
    } else if (key === 'row_source') {
      const labels: Record<string, string> = { object: 'extracted items', evidence: 'supporting evidence', validation_finding: 'validation findings', artifact: 'step results' }
      details.push(`Build download rows from ${labels[String(value)] || 'the default source'}.`)
    } else if (key === 'row_strategy') {
      const labels: Record<string, string> = { object: 'one row per item', object_ledger: 'one row per item with its record details', wide_union: 'combine item types into shared columns' }
      details.push(`Row layout: ${labels[String(value)] || 'default'}.`)
    } else if (key === 'group_by') {
      details.push(Array.isArray(value) && value.length ? `Group rows by ${value.map(fieldName).join(', ')}.` : 'Do not group rows.')
    } else if (key === 'sort') {
      const sort = asList(value)
      details.push(sort.length ? `Sort by ${sort.map((item) => `${fieldName(item.field_ref)} (${item.direction === 'desc' ? 'descending' : 'ascending'})`).join(', ')}.` : 'Keep the default row order.')
    } else if (key === 'missing_value') {
      details.push(`Show missing answers as ${value ? JSON.stringify(value) : 'blank cells'}.`)
    } else if (key === 'source_keys' || key === 'source_extraction_result_ids') {
      details.push('Change which step results are included in the download. Review the selected sources in Technical details.')
    } else {
      details.push(`Download ${key.replaceAll('_', ' ')}: ${typeof value === 'string' ? value : 'updated; see Technical details'}`)
    }
  }
  return details
}

/** Curator-facing view of the exact diff; the full diff remains available below. */
export default function FlowProposalSummary({ proposal }: { proposal: FlowAuthoringProposal }) {
  const { nodes, edges } = proposal.candidate.flow_definition
  const changes: { title: string; details: string[] }[] = []
  const seenNodes = new Set<string>()
  for (const entry of proposal.diff) {
    const node = nodes.find((item) => entry.path === `flow_definition.nodes.${item.id}`
      || entry.path.startsWith(`flow_definition.nodes.${item.id}.`))
    if (node) {
      if (seenNodes.has(node.id)) continue
      seenNodes.add(node.id)
      const prefix = `flow_definition.nodes.${node.id}`
      const entries = proposal.diff.filter((item) => item.path === prefix || item.path.startsWith(`${prefix}.`))
      const added = entries.some((item) => item.path === prefix && item.kind === 'added')
      const name = node.data.agent_display_name || 'Flow step'
      const details = new Set<string>()
      for (const item of entries) {
        const field = item.path.slice(prefix.length + 1).replace(/^data\./, '').split('.')[0]
        const value = item.after
        if (field === 'validation_attachments' && Array.isArray(value)) {
          const before = Array.isArray(item.before) ? item.before : []
          for (const attachment of value) {
            if (!attachment || typeof attachment !== 'object') continue
            const previous = before.find((old) => old?.attachment_id === attachment.attachment_id)
            if (previous?.enabled === attachment.enabled) continue
            const label = attachment.curator_label || attachment.label || 'Validator'
            details.add(`${attachment.enabled ? 'Enable' : 'Disable'}: ${label}`)
          }
          for (const previous of before) {
            if (!value.some((attachment) => attachment?.attachment_id === previous?.attachment_id)) {
              details.add(`Remove validation: ${previous?.curator_label || previous?.label || 'Validator'}`)
            }
          }
        } else if (field === 'projection_plan') {
          projectionDetails(item.path, item.before, item.after, node.data.projection_plan ?? {}).forEach((detail) => details.add(detail))
        } else if (field === 'include_evidence') {
          details.add(value === false ? 'Leave supporting evidence out of the download' : 'Include supporting evidence in the download')
        } else if (field === 'agent_id') {
          details.add(`Use ${name}`)
        } else if (['task_instructions', 'step_goal', 'custom_instructions'].includes(field)) {
          details.add(`${settingNames[field]}: ${typeof value === 'string' && value.trim() ? value : 'None'}`)
        } else if (field && settingNames[field]) {
          details.add(`${settingNames[field]} updated`)
        }
      }
      if (added) {
        if (node.data.agent_description) details.add(node.data.agent_description)
        if (node.data.step_goal) details.add(node.data.step_goal)
        if (node.data.custom_instructions) details.add(`Additional instructions: ${node.data.custom_instructions}`)
        if (node.data.task_instructions) details.add(node.data.task_instructions)
        const validators = node.data.validation_attachments?.filter((attachment) => attachment.state === 'active' && attachment.enabled) ?? []
        if (validators.length) details.add(`${validators.length} automatic validators included. Results will be validated when the flow runs.`)
        for (const attachment of node.data.validation_attachments ?? []) {
          if (attachment.state === 'active' && !attachment.enabled) details.add(`Validation turned off: ${attachment.curator_label || attachment.label || 'Validator'}`)
        }
        if (node.data.projection_plan) projectionDetails('projection_plan', undefined, node.data.projection_plan, node.data.projection_plan).forEach((detail) => details.add(detail))
        if (node.data.include_evidence === false) details.add('Supporting evidence will be left out of the download.')
      }
      changes.push({ title: `${added ? 'Add' : 'Update'} ${name}`, details: [...details] })
      continue
    }
    if (entry.path.startsWith('flow_definition.nodes.') && entry.kind === 'removed'
      && entry.before && typeof entry.before === 'object' && 'data' in entry.before) {
      const data = (entry.before as { data?: { agent_display_name?: string } }).data
      changes.push({ title: `Remove ${data?.agent_display_name || 'step'}`, details: [] })
      continue
    }
    if (entry.path.startsWith('flow_definition.edges.')) {
      const edge = edges.find((item) => entry.path === `flow_definition.edges.${item.id}`
        || entry.path.startsWith(`flow_definition.edges.${item.id}.`))
      if (edge) {
        const name = (id: string) => nodes.find((item) => item.id === id)?.data.agent_display_name || 'step'
        const added = entry.path === `flow_definition.edges.${edge.id}` && entry.kind === 'added'
        changes.push({ title: added
          ? `Connect ${name(edge.source)} to ${name(edge.target)}`
          : `Update the connection from ${name(edge.source)} to ${name(edge.target)}`, details: [] })
      } else {
        changes.push({ title: entry.kind === 'removed' ? 'Remove a connection between steps' : 'Update a connection between steps', details: [] })
      }
      continue
    }
    const label = entry.path === 'name' ? 'Flow name' : entry.path === 'description' ? 'Flow description' : 'Flow settings'
    changes.push({ title: label, details: typeof entry.after === 'string' ? [entry.after || 'None'] : ['Updated for this change'] })
  }
  return (
    <Box component="ul" aria-label="Proposed flow changes" sx={{ m: 0, p: 0, listStyle: 'none' }}>
      {changes.map((change, index) => (
        <Box component="li" key={index} sx={{ py: 1.5, '& + &': { borderTop: 1, borderColor: 'divider' } }}>
          <Typography variant="body1" fontWeight={600}>{change.title}</Typography>
          {change.details.map((detail) => <Typography key={detail} variant="body2" sx={{ mt: 0.5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{detail}</Typography>)}
        </Box>
      ))}
    </Box>
  )
}
