import { describe, expect, it } from 'vitest'

import { resolveRenderAs } from './index'

function field(renderAs?: string, fieldType = 'string') {
  return {
    field_type: fieldType,
    metadata: { field_metadata: renderAs ? { render_as: renderAs } : {} },
  } as any
}

describe('resolveRenderAs', () => {
  it('returns the render_as hint when present', () => {
    expect(resolveRenderAs(field('curie-chip'))).toBe('curie-chip')
    expect(resolveRenderAs(field('sub-table'))).toBe('sub-table')
  })

  it('falls back to a json renderer for array/object field types', () => {
    expect(resolveRenderAs(field(undefined, 'array'))).toBe('json')
    expect(resolveRenderAs(field(undefined, 'object'))).toBe('json')
  })

  it('returns default for plain fields', () => {
    expect(resolveRenderAs(field(undefined, 'string'))).toBe('default')
  })
})
