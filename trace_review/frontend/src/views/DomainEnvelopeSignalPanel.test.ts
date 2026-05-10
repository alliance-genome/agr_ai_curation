import { describe, expect, it } from 'vitest';

import {
  domainEnvelopeCountChips,
  hasDomainEnvelopeSignals,
} from './DomainEnvelopeSignalPanel';
import { DomainEnvelopeTraceSummary } from '../types';

const summary: DomainEnvelopeTraceSummary = {
  found: true,
  summary: {
    envelope_count: 1,
    object_count: 2,
    finding_count: 1,
    repair_attempt_count: 1,
    blocker_count: 1,
  },
  envelope_ids: ['env-1'],
  object_ids: ['object-1'],
  finding_ids: ['finding-1'],
  field_paths: ['gene.symbol'],
};

describe('domain-envelope TraceReview presentation helpers', () => {
  it('detects present and absent domain-envelope summaries', () => {
    expect(hasDomainEnvelopeSignals(summary)).toBe(true);
    expect(hasDomainEnvelopeSignals({ ...summary, found: false })).toBe(false);
    expect(hasDomainEnvelopeSignals(undefined)).toBe(false);
  });

  it('builds count chips with blocker and repair emphasis', () => {
    const chips = domainEnvelopeCountChips(summary);

    expect(chips.map((chip) => chip.label)).toEqual([
      '1 envelopes',
      '2 objects',
      '1 findings',
      '1 repairs',
      '1 blockers',
    ]);
    expect(chips.find((chip) => chip.label === '1 blockers')?.color).toBe('error');
    expect(chips.find((chip) => chip.label === '1 repairs')?.color).toBe('info');
  });
});
