/**
 * A paper's files as the viewer shows them (ALL-1460, interface I5).
 *
 * `GET /api/pdf-viewer/documents/{document_id}/files` lists every file of the paper
 * in tab order. A file has `content_url` only when it is a ready PDF and `text_url`
 * only when it is a ready text file; the viewer gives a tab to each file with a URL.
 * Anything else is a contract error, never guessed.
 */

export type ViewerFileRole = 'main' | 'supplement'
export type ViewerFileDisplayMode = 'local_pdf' | 'provider_pdf_proxy' | 'text'
export type ViewerFileStatus = 'pending' | 'ready' | 'failed'

export interface ViewerFile {
  file_id: string
  role: ViewerFileRole
  display_name: string
  display_mode: ViewerFileDisplayMode
  status: ViewerFileStatus
  tab_order: number
  /** Pages of a ready PDF file; null for a text file or a file that is not ready. */
  page_count: number | null
  /** The file's PDF bytes; only for a ready PDF file. */
  content_url: string | null
  /** The file's display blocks; only for a ready text file. */
  text_url: string | null
}

export interface ViewerFilesPayload {
  document_id: string
  files: ViewerFile[]
}

export type DisplayBlockKind = 'heading' | 'paragraph' | 'list_item' | 'code' | 'table'

export interface DisplayTableRow {
  /** The row exactly as stored in the file's pieces (what a table-row quote equals). */
  line: string
  cells: string[]
  is_header: boolean
}

export interface DisplayBlock {
  kind: DisplayBlockKind
  /**
   * The element's text as stored before cutting into pieces; for a table, its lines
   * (separator line included) joined by line breaks. Every stored piece is an exact
   * slice of the blocks' texts joined by a blank line ("\n\n").
   */
  text: string
  level: number | null
  /** The section names the block's stored pieces carry (the names evidence stores). */
  section_title: string | null
  parent_section: string | null
  subsection: string | null
  section_path: string[]
  rows: DisplayTableRow[] | null
}

export interface ViewerTextPayload {
  document_id: string
  file_id: string
  display_name: string
  blocks: DisplayBlock[]
}

export class ViewerFilesContractError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ViewerFilesContractError'
  }
}

const ROLES: readonly ViewerFileRole[] = ['main', 'supplement']
const DISPLAY_MODES: readonly ViewerFileDisplayMode[] = ['local_pdf', 'provider_pdf_proxy', 'text']
const STATUSES: readonly ViewerFileStatus[] = ['pending', 'ready', 'failed']

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function requireString(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string' || value.length === 0) {
    throw new ViewerFilesContractError(`Viewer file field "${key}" must be a non-empty string`)
  }
  return value
}

function requireText(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string') {
    throw new ViewerFilesContractError(`Viewer file field "${key}" must be a string`)
  }
  return value
}

function requireNullableString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key]
  if (value === null) {
    return null
  }
  if (typeof value !== 'string' || value.length === 0) {
    throw new ViewerFilesContractError(`Viewer file field "${key}" must be a string or null`)
  }
  return value
}

function requireOneOf<T extends string>(
  record: Record<string, unknown>,
  key: string,
  allowed: readonly T[],
): T {
  const value = record[key]
  const match = allowed.find((candidate) => candidate === value)
  if (!match) {
    throw new ViewerFilesContractError(`Viewer file field "${key}" has an unknown value`)
  }
  return match
}

/** Proposal, awaiting sign-off: the label of a file the backend serves with an empty name. */
export const UNNAMED_FILE_LABEL = 'Unnamed file'

/** The name the viewer shows for a file. */
export function fileDisplayLabel(file: Pick<ViewerFile, 'display_name'>): string {
  return file.display_name || UNNAMED_FILE_LABEL
}

export function isPdfDisplayMode(mode: ViewerFileDisplayMode): boolean {
  return mode === 'local_pdf' || mode === 'provider_pdf_proxy'
}

export function isPdfFile(file: ViewerFile): boolean {
  return isPdfDisplayMode(file.display_mode)
}

export function parseViewerFile(value: unknown): ViewerFile {
  if (!isRecord(value)) {
    throw new ViewerFilesContractError('A viewer file must be an object')
  }
  const tabOrder = value.tab_order
  if (typeof tabOrder !== 'number' || !Number.isInteger(tabOrder) || tabOrder < 0) {
    throw new ViewerFilesContractError('Viewer file field "tab_order" must be a whole number of 0 or more')
  }
  const pageCount = value.page_count
  if (pageCount !== null && (typeof pageCount !== 'number' || !Number.isInteger(pageCount) || pageCount < 1)) {
    throw new ViewerFilesContractError('Viewer file field "page_count" must be a whole number of 1 or more, or null')
  }

  const file: ViewerFile = {
    file_id: requireString(value, 'file_id'),
    role: requireOneOf(value, 'role', ROLES),
    // The backend may serve an empty name (a file stored without one); the viewer shows a neutral label.
    display_name: requireText(value, 'display_name'),
    display_mode: requireOneOf(value, 'display_mode', DISPLAY_MODES),
    status: requireOneOf(value, 'status', STATUSES),
    tab_order: tabOrder,
    page_count: pageCount,
    content_url: requireNullableString(value, 'content_url'),
    text_url: requireNullableString(value, 'text_url'),
  }

  const pdf = isPdfFile(file)
  const readyPdf = pdf && file.status === 'ready'
  if (readyPdf ? file.page_count === null : file.page_count !== null) {
    throw new ViewerFilesContractError('Only a ready PDF file has a page count, and it always has one')
  }
  if (file.content_url !== null && (!pdf || file.status !== 'ready')) {
    throw new ViewerFilesContractError('Only a ready PDF file has a content URL')
  }
  if (file.text_url !== null && (pdf || file.status !== 'ready')) {
    throw new ViewerFilesContractError('Only a ready text file has a text URL')
  }
  return file
}

export function parseViewerFiles(value: unknown): ViewerFile[] {
  if (!Array.isArray(value)) {
    throw new ViewerFilesContractError('The paper\'s files must be a list')
  }
  const files = value.map(parseViewerFile)
  for (let index = 1; index < files.length; index += 1) {
    if (files[index]!.tab_order <= files[index - 1]!.tab_order) {
      throw new ViewerFilesContractError('The paper\'s files must be listed in tab order')
    }
  }
  return files
}

export function parseViewerFilesPayload(value: unknown): ViewerFilesPayload {
  if (!isRecord(value)) {
    throw new ViewerFilesContractError('The files payload must be an object')
  }
  const documentId = value.document_id
  if (typeof documentId !== 'string' || documentId.length === 0) {
    throw new ViewerFilesContractError('The files payload must name its paper')
  }
  return { document_id: documentId, files: parseViewerFiles(value.files) }
}

/** The files that get a tab: every file with a URL, in tab order. */
export function viewerTabFiles(files: readonly ViewerFile[]): ViewerFile[] {
  return files.filter((file) => file.content_url !== null || file.text_url !== null)
}

/** The main file when it has a tab; otherwise the first file with a tab; null when none has. */
export function defaultActiveFileId(tabFiles: readonly ViewerFile[]): string | null {
  return (tabFiles.find((file) => file.role === 'main') ?? tabFiles[0])?.file_id ?? null
}

export async function fetchViewerFiles(
  documentId: string,
  signal?: AbortSignal,
): Promise<ViewerFilesPayload> {
  const response = await fetch(`/api/pdf-viewer/documents/${documentId}/files`, {
    credentials: 'same-origin',
    signal,
  })
  if (!response.ok) {
    throw new Error(`Failed to fetch the paper's files (${response.status})`)
  }
  const payload = parseViewerFilesPayload(await response.json())
  if (payload.document_id !== documentId) {
    throw new ViewerFilesContractError('The files payload names a different paper')
  }
  return payload
}

const BLOCK_KINDS: readonly DisplayBlockKind[] = ['heading', 'paragraph', 'list_item', 'code', 'table']

function requireNullableName(record: Record<string, unknown>, key: string): string | null {
  const value = record[key]
  if (value !== null && typeof value !== 'string') {
    throw new ViewerFilesContractError(`Display block field "${key}" must be a name or null`)
  }
  return value
}

function parseDisplayTableRow(value: unknown): DisplayTableRow {
  if (!isRecord(value) || typeof value.line !== 'string' || typeof value.is_header !== 'boolean'
    || !Array.isArray(value.cells) || !value.cells.every((cell) => typeof cell === 'string')) {
    throw new ViewerFilesContractError('A table row must have a line, string cells and a header flag')
  }
  return { line: value.line, cells: value.cells as string[], is_header: value.is_header }
}

function parseDisplayBlock(value: unknown): DisplayBlock {
  if (!isRecord(value)) {
    throw new ViewerFilesContractError('A display block must be an object')
  }
  const kind = requireOneOf(value, 'kind', BLOCK_KINDS)
  const sectionPath = value.section_path
  if (!Array.isArray(sectionPath) || !sectionPath.every((part) => typeof part === 'string')) {
    throw new ViewerFilesContractError('A display block\'s section path must be a list of names')
  }
  const level = value.level
  if (level !== null && (typeof level !== 'number' || !Number.isInteger(level) || level < 1)) {
    throw new ViewerFilesContractError('A display block\'s level must be a whole number of 1 or more, or null')
  }
  if (typeof value.text !== 'string') {
    throw new ViewerFilesContractError('A display block must have its text')
  }
  const sections = {
    section_title: requireNullableName(value, 'section_title'),
    parent_section: requireNullableName(value, 'parent_section'),
    subsection: requireNullableName(value, 'subsection'),
    section_path: sectionPath as string[],
  }
  if (kind === 'table') {
    if (!Array.isArray(value.rows)) {
      throw new ViewerFilesContractError('A table block has rows')
    }
    return { kind, text: value.text, level, ...sections, rows: value.rows.map(parseDisplayTableRow) }
  }
  if (value.rows !== null) {
    throw new ViewerFilesContractError('Only a table block has rows')
  }
  if ((kind === 'heading') !== (level !== null)) {
    throw new ViewerFilesContractError('Only a heading has a level')
  }
  return { kind, text: value.text, level, ...sections, rows: null }
}

export function parseViewerTextPayload(value: unknown): ViewerTextPayload {
  if (!isRecord(value) || !Array.isArray(value.blocks)) {
    throw new ViewerFilesContractError('The text payload must hold a list of blocks')
  }
  return {
    document_id: requireString(value, 'document_id'),
    file_id: requireString(value, 'file_id'),
    // The backend may serve an empty name (a file stored without one); the viewer shows a neutral label.
    display_name: requireText(value, 'display_name'),
    blocks: value.blocks.map(parseDisplayBlock),
  }
}

export async function fetchViewerText(
  file: { fileId: string; textUrl: string | null },
  signal?: AbortSignal,
): Promise<ViewerTextPayload> {
  if (file.textUrl === null) {
    throw new ViewerFilesContractError(`File ${file.fileId} has no text to show`)
  }
  const response = await fetch(file.textUrl, { credentials: 'same-origin', signal })
  if (!response.ok) {
    throw new Error(`Failed to fetch the file's text (${response.status})`)
  }
  const payload = parseViewerTextPayload(await response.json())
  if (payload.file_id !== file.fileId) {
    throw new ViewerFilesContractError('The text payload names a different file')
  }
  return payload
}
