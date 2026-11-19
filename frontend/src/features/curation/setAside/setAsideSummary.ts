/**
 * Records the automatic review set aside in a finished flow run (ALL-1561).
 *
 * A paper still finishes when its review sets records aside. The run summary
 * (FLOW_FINISHED `set_aside`, batch `review_details.set_aside`) says how many
 * there are and, per output, whether they are in it. This module reads that
 * summary and holds the curator-facing wording shared by the batch page, the
 * chat completion card and the viewer.
 */

export interface FlowSetAsideOutput {
  type: string
  formatter_node_id: string | null
  file_id: string | null
  filename: string | null
  included: number
  left_out: number
}

export interface FlowSetAsideResultCount {
  extraction_result_id: string | null
  node_id: string | null
  episode_id: string
  count: number
}

export interface FlowSetAsideSummary {
  count: number
  results: FlowSetAsideResultCount[]
  outputs: FlowSetAsideOutput[]
  left_out_of_outputs: number
  included_in_outputs: number
}

/**
 * Where the set-aside records ended up:
 * - `left_out`: every output with them left them out;
 * - `included`: every output with them includes them, marked "set aside";
 * - `mixed`: some outputs include them and some leave them out;
 * - `no_output`: the run wrote no file or chat output (for example a curation handoff only).
 */
export type SetAsidePlacement = 'left_out' | 'included' | 'mixed' | 'no_output'

const isCount = (value: unknown): value is number => (
  typeof value === 'number' && Number.isInteger(value) && value >= 0
)

const isNullableString = (value: unknown): boolean => value === null || typeof value === 'string'

function isOutput(value: unknown): value is FlowSetAsideOutput {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.type === 'string'
    && isNullableString(candidate.formatter_node_id)
    && isNullableString(candidate.file_id)
    && isNullableString(candidate.filename)
    && isCount(candidate.included)
    && isCount(candidate.left_out)
}

function isResultCount(value: unknown): value is FlowSetAsideResultCount {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return isNullableString(candidate.extraction_result_id)
    && isNullableString(candidate.node_id)
    && typeof candidate.episode_id === 'string'
    && isCount(candidate.count)
}

/** Structural check of a run's set-aside summary. */
export function isFlowSetAsideSummary(value: unknown): value is FlowSetAsideSummary {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return isCount(candidate.count)
    && candidate.count > 0
    && Array.isArray(candidate.results) && candidate.results.every(isResultCount)
    && Array.isArray(candidate.outputs) && candidate.outputs.every(isOutput)
    && isCount(candidate.left_out_of_outputs)
    && isCount(candidate.included_in_outputs)
}

function placementFromCounts(leftOut: number, included: number): SetAsidePlacement {
  if (leftOut > 0) return included > 0 ? 'mixed' : 'left_out'
  return included > 0 ? 'included' : 'no_output'
}

export function setAsidePlacement(summary: FlowSetAsideSummary): SetAsidePlacement {
  return placementFromCounts(summary.left_out_of_outputs, summary.included_in_outputs)
}

/** One output's own counts (FILE_READY / CHAT_OUTPUT_READY `details.set_aside`). */
export interface OutputSetAsideCounts {
  included: number
  left_out: number
}

export function isOutputSetAsideCounts(value: unknown): value is OutputSetAsideCounts {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return isCount(candidate.included) && isCount(candidate.left_out)
    && candidate.included + candidate.left_out > 0
}

/**
 * A file's set-aside counts from FILE_READY details or a saved file message:
 * null when the file's sources hold none. A malformed value is reported and
 * not shown, like other malformed stream payloads.
 */
export function readFileSetAside(value: unknown): OutputSetAsideCounts | null {
  if (value === null || value === undefined) return null
  if (isOutputSetAsideCounts(value)) return value
  console.warn('[set-aside] Ignoring a malformed set-aside count on a file', value)
  return null
}

export function outputSetAsidePlacement(counts: OutputSetAsideCounts): SetAsidePlacement {
  return placementFromCounts(counts.left_out, counts.included)
}

/** Line on a chat file card whose sources hold set-aside records. */
export function fileSetAsideLabel(counts: OutputSetAsideCounts): string {
  return counts.left_out > 0
    ? `${recordCountText(counts.left_out)} set aside by review, not in this file`
    : `${recordCountText(counts.included)} set aside by review, in this file`
}

/** True when some output left the records out, so the curator may want to add them. */
export function isLeftOutSomewhere(placement: SetAsidePlacement): boolean {
  return placement === 'left_out' || placement === 'mixed'
}

export function recordCountText(count: number): string {
  return `${count} record${count === 1 ? '' : 's'}`
}

/** Row label on the batch page, and the title of the chat notice strip. */
export function setAsideFinishedLabel(summary: FlowSetAsideSummary): string {
  const records = recordCountText(summary.count)
  switch (setAsidePlacement(summary)) {
    case 'left_out':
      return `Finished, ${records} set aside, not in the file`
    case 'mixed':
      return `Finished, ${records} set aside, not in every file`
    case 'included':
    case 'no_output':
      return `Finished, ${records} set aside by review`
  }
}

/** One Audit Log line for a paper whose review set records aside. */
export function setAsideAuditMessage(paperTitle: string, summary: FlowSetAsideSummary): string {
  const records = recordCountText(summary.count)
  switch (setAsidePlacement(summary)) {
    case 'left_out':
      return `${paperTitle}: ${records} set aside by review, not in the file`
    case 'mixed':
      return `${paperTitle}: ${records} set aside by review, not in every file`
    case 'included':
      return `${paperTitle}: ${records} set aside by review, in the file`
    case 'no_output':
      return `${paperTitle}: ${records} set aside by review`
  }
}

/** Button text that opens the viewer. Quieter when nothing is missing from a file. */
export function setAsideViewButtonLabel(summary: FlowSetAsideSummary): string {
  if (!isLeftOutSomewhere(setAsidePlacement(summary))) return 'View'
  return summary.count === 1 ? 'View set-aside record' : 'View set-aside records'
}

/** Short tag on each record in the viewer; null when the run wrote no output. */
export function setAsidePlacementTag(placement: SetAsidePlacement): string | null {
  switch (placement) {
    case 'left_out':
      return 'Not in the file'
    case 'mixed':
      return 'Not in every file'
    case 'included':
      return 'In the file'
    case 'no_output':
      return null
  }
}

export const SET_ASIDE_INCLUDE_ADVICE = 'You can include these records in your files. Open your flow, choose output fields, and add Review status, Review reason and Review uncertainty.'
export const SET_ASIDE_ALREADY_IN_FILE = 'These records are already in your file, marked "set aside" in the Review status column.'
export const SET_ASIDE_UNPROFILED_NOTE = 'These records come from a general extraction that has no saved output fields. They cannot be added to this flow\'s files yet, so they are shown here only.'
export const SET_ASIDE_READ_ONLY_NOTE = 'Read only. Nothing here changes your results.'
