import { describe, expect, it } from 'vitest'

import {
  type FlowSetAsideSummary,
  isFlowSetAsideSummary,
  setAsideAuditMessage,
  setAsideFinishedLabel,
  setAsidePlacement,
  setAsideViewButtonLabel,
} from './setAsideSummary'

function summary(count: number, leftOut: number, included: number, withOutputs = true): FlowSetAsideSummary {
  return {
    count,
    results: [{ extraction_result_id: 'r-1', node_id: 'extract', episode_id: 'ep-1', count }],
    outputs: withOutputs
      ? [{ type: 'FILE_READY', formatter_node_id: 'out', file_id: 'f-1', filename: 'out.tsv', included, left_out: leftOut }]
      : [],
    left_out_of_outputs: leftOut,
    included_in_outputs: included,
  }
}

describe('setAsideSummary', () => {
  it('reads where the records ended up from the run summary', () => {
    expect(setAsidePlacement(summary(1, 1, 0))).toBe('left_out')
    expect(setAsidePlacement(summary(1, 0, 1))).toBe('included')
    expect(setAsidePlacement(summary(1, 1, 1))).toBe('mixed')
    expect(setAsidePlacement(summary(1, 0, 0, false))).toBe('no_output')
  })

  it('words the row label, button and audit line with the count', () => {
    expect(setAsideFinishedLabel(summary(1, 1, 0))).toBe('Finished, 1 record set aside, not in the file')
    expect(setAsideFinishedLabel(summary(2, 2, 0))).toBe('Finished, 2 records set aside, not in the file')
    expect(setAsideFinishedLabel(summary(2, 2, 2))).toBe('Finished, 2 records set aside, not in every file')
    expect(setAsideFinishedLabel(summary(1, 0, 1))).toBe('Finished, 1 record set aside by review')
    expect(setAsideViewButtonLabel(summary(1, 1, 0))).toBe('View set-aside record')
    expect(setAsideViewButtonLabel(summary(3, 3, 0))).toBe('View set-aside records')
    expect(setAsideViewButtonLabel(summary(1, 0, 1))).toBe('View')
    expect(setAsideAuditMessage('Document 3', summary(1, 1, 0)))
      .toBe('Document 3: 1 record set aside by review, not in the file')
    expect(setAsideAuditMessage('Document 3', summary(2, 0, 2)))
      .toBe('Document 3: 2 records set aside by review, in the file')
  })

  it('accepts only a well-formed summary', () => {
    expect(isFlowSetAsideSummary(summary(1, 1, 0))).toBe(true)
    expect(isFlowSetAsideSummary(null)).toBe(false)
    expect(isFlowSetAsideSummary({ ...summary(1, 1, 0), count: 0 })).toBe(false)
    expect(isFlowSetAsideSummary({ ...summary(1, 1, 0), outputs: [{ type: 'FILE_READY' }] })).toBe(false)
    expect(isFlowSetAsideSummary({ ...summary(1, 1, 0), left_out_of_outputs: '1' })).toBe(false)
  })
})
