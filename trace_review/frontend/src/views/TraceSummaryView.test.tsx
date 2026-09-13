import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { TraceSummaryData } from '../types';
import { TraceSummaryView } from './TraceSummaryView';

const summary: TraceSummaryData = {
  trace_info: {
    trace_id: 'trace-groups',
    name: 'Group context trace',
    timestamp: '2026-09-13T00:00:00Z',
    tags: [],
    bookmarked: false,
  },
  query: 'Summarize the document.',
  timing: {
    total_latency_seconds: 0,
    created_at: '2026-09-13T00:00:00Z',
    updated_at: '2026-09-13T00:00:00Z',
  },
  cost: { total_cost: 0, currency: 'USD' },
  generation_stats: {
    total_generations: 0,
    total_prompt_tokens: 0,
    total_completion_tokens: 0,
    total_tokens: 0,
    models_used: {},
  },
  tool_summary: { total_tool_calls: 0, tool_counts: {}, unique_tools: [] },
  errors: [],
  has_errors: false,
  context_overflow_detected: false,
  agent_info: {},
  links: {},
};

describe('TraceSummaryView group context', () => {
  it.each([
    { groups: ['group-alpha'], countLabel: '1 Group Active' },
    { groups: ['group-alpha', 'FB'], countLabel: '2 Groups Active' },
  ])('renders opaque group chips with $countLabel', ({ groups, countLabel }) => {
    const markup = renderToStaticMarkup(
      <TraceSummaryView data={{
        ...summary,
        group_context: {
          active_groups: groups,
          injection_active: true,
          group_count: groups.length,
        },
      }} />,
    );

    expect(markup).toContain('Group Context');
    expect(markup).toContain(countLabel);
    const outlinedChipLabels = [...markup.matchAll(
      /<div class="[^"]*MuiChip-outlined[^"]*">.*?<span class="[^"]*MuiChip-label[^"]*">([^<]*)<\/span><\/div>/g,
    )].map((match) => match[1]);
    expect(outlinedChipLabels).toEqual(groups);
  });

  it('hides group context when injection is inactive', () => {
    const markup = renderToStaticMarkup(
      <TraceSummaryView data={{
        ...summary,
        group_context: {
          active_groups: ['group-alpha'],
          injection_active: false,
          group_count: 1,
        },
      }} />,
    );

    expect(markup).not.toContain('Group Context');
    expect(markup).not.toContain('group-alpha');
    expect(markup).not.toContain('1 Group Active');
  });

  it('hides group context when the optional context is absent', () => {
    const markup = renderToStaticMarkup(<TraceSummaryView data={summary} />);

    expect(markup).not.toContain('Group Context');
  });
});
