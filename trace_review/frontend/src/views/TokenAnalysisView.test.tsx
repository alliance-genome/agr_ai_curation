import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { TokenAnalysisData } from '../types';
import { TokenAnalysisView } from './TokenAnalysisView';

const providerUsageOnly: TokenAnalysisData = {
  found: true,
  total_cost: 0,
  total_latency: 0,
  total_generations: 0,
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  generations: [],
  context_growth: [],
  model_breakdown: {},
  provider_usage: [
    {
      requested_provider: 'openrouter',
      requested_model: 'deepseek/deepseek-v4-pro-0813',
      actual_provider: null,
      actual_model: null,
      routing_attempt: 1,
      latency_ms: 1234,
      input_tokens: 10,
      output_tokens: 20,
      total_tokens: 30,
      billed_cost: {
        amount: '0.0012300',
        unit: 'credits',
        source: 'openrouter_usage',
      },
    },
  ],
  context_overflow_detected: false,
};

describe('TokenAnalysisView provider usage', () => {
  it('renders a provider-usage-only payload without inventing an actual route', () => {
    const markup = renderToStaticMarkup(<TokenAnalysisView data={providerUsageOnly} />);

    expect(markup).toContain('Provider Usage');
    expect(markup).toContain('openrouter / deepseek/deepseek-v4-pro-0813');
    expect(markup).toContain('Absent');
    expect(markup).toContain('0.0012300 credits');
    expect(markup).toContain('openrouter_usage');
  });

  it('preserves the no-token-data panel when provider usage is absent', () => {
    const markup = renderToStaticMarkup(
      <TokenAnalysisView data={{ ...providerUsageOnly, found: false, provider_usage: [] }} />,
    );

    expect(markup).toContain('No token data found in this trace');
  });
});
