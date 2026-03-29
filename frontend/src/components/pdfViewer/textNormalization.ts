export interface NormalizedTextSourceMap {
  text: string
  sourceIndices: number[]
}

const SOFT_HYPHEN = '\u00ad'
const NBSP = '\u00a0'
const ELLIPSIS_PATTERN = /(?:\u2026|\.{3,})/g
const DASH_PATTERN = /[\u2010\u2011\u2012\u2013\u2014\u2212]/g
const SINGLE_QUOTE_PATTERN = /[\u2018\u2019\u201A\u201B]/g
const DOUBLE_QUOTE_PATTERN = /[\u201C\u201D\u201E\u201F]/g
const OPENING_BRACKETS = new Set(['(', '[', '{'])
const TRAILING_SPACE_PUNCTUATION = new Set([',', '.', ';', ':', '!', '?', ')', ']', '}'])
const INLINE_MARKDOWN_WRAPPER_PATTERNS: Array<[RegExp, string]> = [
  [/(^|[\s([{])\*\*([^*]+?)\*\*(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])\*([^*]+?)\*(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])__([^_]+?)__(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])_([^_]+?)_(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])`([^`]+?)`(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
]

const isWhitespaceCharacter = (value: string): boolean => /\s/.test(value)

export const sanitizeEvidenceSearchText = (value: string): string => {
  if (!value) {
    return ''
  }

  let sanitized = value.replace(ELLIPSIS_PATTERN, ' ')

  // Evidence quotes can pick up lightweight markdown emphasis from model output
  // even though the underlying PDF text is plain. Strip only wrapper syntax and
  // keep the quoted content intact for downstream matching.
  for (let index = 0; index < 2; index += 1) {
    let nextValue = sanitized
    INLINE_MARKDOWN_WRAPPER_PATTERNS.forEach(([pattern, replacement]) => {
      nextValue = nextValue.replace(pattern, replacement)
    })
    if (nextValue === sanitized) {
      break
    }
    sanitized = nextValue
  }

  return sanitized
}

const transformNormalizedCharacter = (value: string): string => {
  if (value === SOFT_HYPHEN) {
    return ''
  }

  if (value === NBSP) {
    return ' '
  }

  if (value === '\r' || value === '\n') {
    return ' '
  }

  return value
    .replace(DASH_PATTERN, '-')
    .replace(SINGLE_QUOTE_PATTERN, "'")
    .replace(DOUBLE_QUOTE_PATTERN, '"')
}

export const buildNormalizedTextSourceMap = (value: string): NormalizedTextSourceMap => {
  const output: string[] = []
  const sourceIndices: number[] = []

  for (let index = 0; index < value.length;) {
    const codePoint = value.codePointAt(index)
    const codeUnitLength = codePoint !== undefined && codePoint > 0xffff ? 2 : 1
    const rawChunk = value.slice(index, index + codeUnitLength).normalize('NFKC')

    for (const normalizedCharacter of rawChunk) {
      const nextCharacter = transformNormalizedCharacter(normalizedCharacter)

      for (const character of nextCharacter) {
        if (character.length === 0) {
          continue
        }

        if (isWhitespaceCharacter(character)) {
          const previous = output[output.length - 1]

          if (previous === undefined || isWhitespaceCharacter(previous) || OPENING_BRACKETS.has(previous)) {
            continue
          }

          output.push(' ')
          sourceIndices.push(index)
          continue
        }

        const previous = output[output.length - 1]
        if (previous === ' ' && TRAILING_SPACE_PUNCTUATION.has(character)) {
          output.pop()
          sourceIndices.pop()
        }

        output.push(character)
        sourceIndices.push(index)
      }
    }

    index += codeUnitLength
  }

  while (output.length > 0 && output[output.length - 1] === ' ') {
    output.pop()
    sourceIndices.pop()
  }

  return {
    text: output.join(''),
    sourceIndices,
  }
}

export const normalizeTextForEvidenceMatch = (value: string): string => {
  return buildNormalizedTextSourceMap(value).text
}

export const splitNormalizedWords = (value: string): string[] => {
  const normalized = normalizeTextForEvidenceMatch(value)
  return normalized.length > 0 ? normalized.split(/\s+/).filter(Boolean) : []
}

export const extractSentenceCandidate = (value: string): string | null => {
  const normalized = normalizeTextForEvidenceMatch(value)
  const match = normalized.match(/^(.{40,}?[.!?])(?:\s|$)/)
  return match?.[1]?.trim() ?? null
}
