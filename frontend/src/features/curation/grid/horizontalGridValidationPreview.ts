import type { FieldStateKind } from '@/features/curation/editor/fieldState'
import type {
  HorizontalGridFieldCell,
  HorizontalGridModel,
} from './horizontalGridModel'

export interface HorizontalGridValidationPreviewCounts {
  total: number
  resolved: number
  needsReview: number
  notValidated: number
}

export function horizontalGridValidationPreviewKey(
  candidateId: string,
  fieldPath: string,
): string {
  return JSON.stringify([candidateId, fieldPath])
}

export function toggledHorizontalGridValidationPreviewState(
  current: FieldStateKind,
): FieldStateKind {
  return current === 'resolved' ? 'needs-review' : 'resolved'
}

export function applyHorizontalGridValidationPreview(
  model: HorizontalGridModel,
  overrides: ReadonlyMap<string, FieldStateKind>,
): HorizontalGridModel {
  if (overrides.size === 0) {
    return model
  }

  return {
    ...model,
    rows: model.rows.map((row) => ({
      ...row,
      cells: row.cells.map((cell) => {
        if (!cell.hasField || cell.state === null) {
          return cell
        }

        const override = overrides.get(
          horizontalGridValidationPreviewKey(row.candidateId, cell.fieldPath),
        )
        return override === undefined ? cell : { ...cell, state: override }
      }),
    })),
  }
}

export function horizontalGridValidationPreviewCounts(
  model: HorizontalGridModel,
): HorizontalGridValidationPreviewCounts {
  const counts: HorizontalGridValidationPreviewCounts = {
    total: 0,
    resolved: 0,
    needsReview: 0,
    notValidated: 0,
  }

  for (const row of model.rows) {
    for (const cell of row.cells) {
      countHorizontalGridValidationPreviewCell(counts, cell)
    }
  }

  return counts
}

function countHorizontalGridValidationPreviewCell(
  counts: HorizontalGridValidationPreviewCounts,
  cell: HorizontalGridFieldCell,
): void {
  if (!cell.hasField || cell.state === null) {
    return
  }

  counts.total += 1
  if (cell.state === 'resolved') {
    counts.resolved += 1
  } else if (cell.state === 'needs-review') {
    counts.needsReview += 1
  } else {
    counts.notValidated += 1
  }
}
