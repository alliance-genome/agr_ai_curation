const records = [
  {
    id: 'ninae-expression',
    label: 'Figure 1C · ninaE',
    page: 1,
    quote: 'The apical domain of a photoreceptor cell, termed rhabdomere, is highly enriched with proteins of the phototransduction machinery.',
    fieldEvidence: {
      gene: 'NinaE is the most abundant rhodopsin in the adult Drosophila eye and a major component of the phototransduction machinery.',
      relationship: 'The apical domain of a photoreceptor cell, termed rhabdomere, is highly enriched with proteins of the phototransduction machinery.',
      structure: 'The apical domain of a photoreceptor cell, termed rhabdomere, is highly enriched with proteins of the phototransduction machinery.',
      gocc: 'Phototransduction proteins are concentrated in the specialized apical compartment that forms the photoreceptor outer segment.',
      assay: 'Absolute (molar) quantification of proteins determines their molar ratios in the complex network of the phototransduction pathway.',
    },
    highlight: { top: 25.5, left: 7.5, width: 46, height: 5.3 },
    fields: {
      figure: field('Figure', '1C', 'Figure panel', 'neutral'),
      gene: field('Gene', 'ninaE', 'FBgn0002940', 'validated'),
      relationship: field('Relationship', 'is enriched in', 'RO:0002434', 'validated'),
      structure: field('Structure', 'rhabdomere', 'FBbt:00007452', 'validated'),
      gocc: field('GOCC', 'photoreceptor outer segment', 'GO:0001750', 'review'),
      when: field('When', 'adult', 'FBdv:00005369', 'validated'),
      genotype: field('Genotype', 'wild type', 'FB:WT0001', 'validated'),
      conditions: field('Conditions', 'standard laboratory conditions', 'ZECO:0000103', 'validated'),
      assay: field('Assay', 'MS Western', 'ECO:0000160', 'validated'),
      statement: field('Where expressed statement', 'NinaE is abundant in the phototransduction machinery of the rhabdomere.', 'Page 1 · Introduction', 'validated'),
      notes: field('Notes', 'Direct protein quantification from adult fly eyes.', 'Curator note', 'neutral'),
    },
  },
  {
    id: 'crb-expression',
    label: 'Figure 1A–B · crb',
    page: 1,
    quote: 'The integrity and morphology of rhabdomeres critically depends, among other proteins, on the evolutionarily conserved gene crumbs (crb).',
    fieldEvidence: {
      gene: 'The integrity and morphology of rhabdomeres critically depends, among other proteins, on the evolutionarily conserved gene crumbs (crb).',
      relationship: 'Crumbs is expressed in photoreceptor cells and is required to maintain the integrity and morphology of their rhabdomeres.',
      structure: 'The integrity and morphology of rhabdomeres critically depends, among other proteins, on the evolutionarily conserved gene crumbs (crb).',
      gocc: 'Crumbs localizes at the apical plasma membrane of photoreceptor cells, adjacent to the rhabdomere.',
      genotype: 'Loss of crb function disrupts rhabdomere morphology and apical membrane organization in the adult eye.',
      conditions: 'Rhabdomere morphology was compared between wild-type and crb mutant photoreceptor cells.',
    },
    highlight: { top: 39.3, left: 53.5, width: 39.5, height: 5.1 },
    fields: {
      figure: field('Figure', '1A–B', 'Figure panels', 'neutral'),
      gene: field('Gene', 'crb', 'FBgn0259685', 'validated'),
      relationship: field('Relationship', 'is expressed in', 'RO:0002292', 'validated'),
      structure: field('Structure', 'photoreceptor cell', 'FBbt:00004213', 'validated'),
      gocc: field('GOCC', 'apical plasma membrane', 'GO:0016324', 'validated'),
      when: field('When', 'adult', 'FBdv:00005369', 'validated'),
      genotype: field('Genotype', 'crb11A22', 'not resolved', 'error', 'Genotype term not resolved'),
      conditions: field('Conditions', 'rhabdomere morphogenesis assay', 'FBcv:0000398', 'review'),
      assay: field('Assay', 'MS Western', 'ECO:0000160', 'validated'),
      statement: field('Where expressed statement', 'Crb supports rhabdomere integrity in adult photoreceptor cells.', 'Page 1 · Introduction', 'validated'),
      notes: field('Notes', 'Compare mutant protein abundance with wild type.', 'Curator note', 'neutral'),
    },
  },
  {
    id: 'arr2-expression',
    label: 'Figure 2 · Arr2',
    page: 2,
    quote: 'The molar content of phototransduction proteins spans a broad dynamic range, with Phosrestin-1 (Arr-2) among the abundant measured components.',
    fieldEvidence: {
      gene: 'Phosrestin-1 (Arr-2) was detected among the abundant measured components of the adult eye.',
      relationship: 'The molar content of phototransduction proteins spans a broad dynamic range, with Phosrestin-1 (Arr-2) among the abundant measured components.',
      structure: 'The quantified phototransduction proteins were isolated from compound eyes of adult Drosophila.',
      gocc: 'Arr-2 associates with activated rhodopsin in the rhabdomere during phototransduction.',
      assay: 'Protein abundance was determined by absolute quantitative LC–MS/MS measurements.',
      statement: 'The molar content of phototransduction proteins spans a broad dynamic range, with Phosrestin-1 (Arr-2) among the abundant measured components.',
    },
    highlight: { top: 18.5, left: 8, width: 84, height: 4.8 },
    fields: {
      figure: field('Figure', '2', 'Quantification plot', 'neutral'),
      gene: field('Gene', 'Arr2', 'FBgn0000120', 'validated'),
      relationship: field('Relationship', 'is enriched in', 'RO:0002434', 'validated'),
      structure: field('Structure', 'compound eye', 'FBbt:00004508', 'validated'),
      gocc: field('GOCC', 'rhabdomere', 'GO:0016028', 'review'),
      when: field('When', 'adult', 'FBdv:00005369', 'validated'),
      genotype: field('Genotype', 'wild type', 'FB:WT0001', 'validated'),
      conditions: field('Conditions', 'quantitative proteomics', 'ZECO:0000103', 'validated'),
      assay: field('Assay', 'LC–MS/MS', 'MMO:0000667', 'validated'),
      statement: field('Where expressed statement', 'Arr2 is an abundant component of the adult eye phototransduction machinery.', 'Page 2 · Results', 'review'),
      notes: field('Notes', 'Confirm whether the statement should describe protein abundance or gene expression.', 'Curator note', 'neutral'),
    },
  },
]

const fieldOrder = [
  'figure',
  'gene',
  'relationship',
  'structure',
  'gocc',
  'when',
  'genotype',
  'conditions',
  'assay',
  'statement',
  'notes',
]

function field(label, value, identifier, status, message = '') {
  return { label, value, identifier, status, message }
}

const workspace = document.querySelector('#workspace')
const pdfPanel = document.querySelector('#pdf-panel')
const resizeHandle = document.querySelector('#resize-handle')
const rowContainer = document.querySelector('#curation-rows')
const tableScroll = document.querySelector('#curation-grid')
const curationTable = document.querySelector('.curation-table')
const tableColgroup = curationTable.querySelector('colgroup')
const tableHead = document.querySelector('.curation-table thead')
const selectedLabel = document.querySelector('#selected-label')
const evidenceQuote = document.querySelector('#evidence-quote')
const evidenceCaptionLabel = document.querySelector('.evidence-caption-label')
const pdfHighlight = document.querySelector('#pdf-highlight')
const pdfPageImage = document.querySelector('#pdf-page-image')
const pdfSheet = document.querySelector('#pdf-sheet')
const pageInput = document.querySelector('#page-input')
const zoomValue = document.querySelector('#zoom-value')
const focusButton = document.querySelector('#focus-grid')
const popover = document.querySelector('#validation-popover')
const popoverTitle = document.querySelector('#popover-title')
const popoverDescription = document.querySelector('#popover-description')
const popoverMeta = document.querySelector('#popover-meta')
const popoverStatusIcon = document.querySelector('#popover-status-icon')
const editDialog = document.querySelector('#edit-dialog')
const editForm = document.querySelector('#edit-form')
const editTitle = document.querySelector('#edit-title')
const editInput = document.querySelector('#edit-input')
const summaryDrawer = document.querySelector('#summary-drawer')
const toast = document.querySelector('#toast')
const densityButtons = document.querySelectorAll('[data-density]')
const summaryFieldTotal = document.querySelector('#summary-field-total')
const summaryValidatedCount = document.querySelector('#summary-validated-count')
const summaryReviewCount = document.querySelector('#summary-review-count')
const summaryErrorCount = document.querySelector('#summary-error-count')
const clearPinsButton = document.querySelector('#clear-pins')

let selectedRecordId = records[0].id
let activeCell = null
let currentPage = 1
let currentZoom = 100
let focusMode = false
let toastTimer = null
const pinnedColumns = new Set(['figure'])

function statusSymbol(status) {
  if (status === 'validated') return '✓'
  if (status === 'review') return '!'
  if (status === 'error') return '×'
  return ''
}

function statusLabel(status) {
  if (status === 'validated') return 'Curator validated'
  if (status === 'review') return 'Needs review'
  if (status === 'error') return 'Not validated'
  return 'Context field; validation not required'
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function actionIcon(name) {
  const icons = {
    evidence: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 5.5h13v9h-13z"/><path d="M6 8h8M6 11h5"/></svg>',
    validate: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 10.5 3.1 3.1L15.5 6"/></svg>',
    edit: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 14.8.7-3.4L13 3.1l3 3-8.3 8.3zM11.8 4.3l3 3M4.6 15.4l3.1-1"/></svg>',
  }
  return icons[name]
}

function renderRecords() {
  rowContainer.innerHTML = records.map((record) => {
    const cells = fieldOrder.map((key) => {
      const item = record.fields[key]
      const cellClass = key === 'figure'
        ? 'figure-cell'
        : key === 'gene'
          ? 'gene-cell'
          : ''
      const errorMessage = item.message
        ? `<span class="field-message" role="img" aria-label="${escapeHtml(item.message)}" title="${escapeHtml(item.message)}"><span class="field-message-icon" aria-hidden="true">!</span><span class="field-message-text">${escapeHtml(item.message)}</span></span>`
        : ''
      const evidenceAction = `
        <button class="cell-action-button evidence-action" type="button" data-cell-action="evidence"
          aria-label="Show paper evidence for ${escapeHtml(item.label)}: ${escapeHtml(item.value)}"
          title="Evidence & validation details">${actionIcon('evidence')}</button>`
      const editAction = `
        <button class="cell-action-button edit-action" type="button" data-cell-action="edit"
          aria-label="Edit ${escapeHtml(item.label)}: ${escapeHtml(item.value)}"
          title="Edit value">${actionIcon('edit')}</button>`
      const validationAction = item.status === 'neutral' ? '' : `
        <button class="cell-action-button validation-action ${item.status === 'validated' ? 'is-validated' : 'is-unvalidated'}"
          type="button" data-cell-action="toggle-validation" aria-pressed="${item.status === 'validated'}"
          aria-label="${item.status === 'validated' ? 'Mark as not validated' : 'Validate'} ${escapeHtml(item.label)}: ${escapeHtml(item.value)}"
          title="${item.status === 'validated' ? 'Curator validated — click to mark not validated' : 'Not validated — click to validate'}">${actionIcon('validate')}</button>`
      const cellActions = key === 'figure'
        ? evidenceAction
        : key === 'notes'
          ? editAction
          : `${evidenceAction}${validationAction}${editAction}`

      return `
        <td class="${cellClass}" data-column-key="${key}">
          <div
            class="field-cell ${item.status}"
            data-record-id="${record.id}"
            data-field-key="${key}"
            aria-label="${escapeHtml(item.label)}: ${escapeHtml(item.value)}. ${statusLabel(item.status)}."
          >
            <span class="field-value">${escapeHtml(item.value)}</span>
            <span class="field-id">${escapeHtml(item.identifier)}</span>
            ${errorMessage}
            <div class="cell-actions" role="group" aria-label="Actions for ${escapeHtml(item.label)}: ${escapeHtml(item.value)}">
              ${cellActions}
            </div>
          </div>
        </td>
      `
    }).join('')

    const curatedFields = Object.values(record.fields).filter((item) => item.status !== 'neutral')
    const validatedCount = curatedFields.filter((item) => item.status === 'validated').length
    const rowIsValidated = validatedCount === curatedFields.length
    const rowAction = `
      <td class="sticky-right row-action-cell">
        <div class="row-action-wrap">
          <button
            class="validate-row-button"
            type="button"
            data-validate-row="${record.id}"
            aria-label="Validate all curated fields in ${escapeHtml(record.label)}"
            ${rowIsValidated ? 'disabled' : ''}
          >${rowIsValidated ? 'Validated' : 'Validate'}</button>
          <span class="row-validation-progress" aria-label="${validatedCount} of ${curatedFields.length} fields validated">${validatedCount}/${curatedFields.length}</span>
        </div>
      </td>
    `

    return `<tr data-record-id="${record.id}" class="${record.id === selectedRecordId ? 'selected' : ''}">${cells}${rowAction}</tr>`
  }).join('')
  applyPinnedColumns()
}

function displayFieldOrder() {
  const activePinnedKeys = [...pinnedColumns].filter((key) => fieldOrder.includes(key))
  const unpinnedKeys = fieldOrder.filter((key) => !pinnedColumns.has(key))
  return [...activePinnedKeys, ...unpinnedKeys]
}

function captureColumnPositions() {
  return new Map(
    [...curationTable.querySelectorAll('th[data-column-key], td[data-column-key]')]
      .map((element) => [element, element.getBoundingClientRect()]),
  )
}

function reorderTableColumns(orderedKeys) {
  const headerRow = tableHead.querySelector('tr')
  const rowActionHeader = headerRow.querySelector('.row-action-heading')
  const rowActionCol = tableColgroup.querySelector('.row-action-col')

  orderedKeys.forEach((key) => {
    const header = headerRow.querySelector(`[data-column-key="${key}"]`)
    const column = tableColgroup.querySelector(`[data-column-key="${key}"]`)
    if (header) headerRow.insertBefore(header, rowActionHeader)
    if (column) tableColgroup.insertBefore(column, rowActionCol)
  })

  rowContainer.querySelectorAll('tr').forEach((row) => {
    const rowAction = row.querySelector('.row-action-cell')
    orderedKeys.forEach((key) => {
      const cell = row.querySelector(`[data-column-key="${key}"]`)
      if (cell) row.insertBefore(cell, rowAction)
    })
  })
}

function animateColumnReorder(previousPositions, changedColumnKey) {
  if (!previousPositions || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

  requestAnimationFrame(() => {
    previousPositions.forEach((previousRect, element) => {
      if (!element.isConnected) return
      const currentRect = element.getBoundingClientRect()
      const offsetX = previousRect.left - currentRect.left
      if (Math.abs(offsetX) < 1) return

      element.getAnimations().forEach((animation) => animation.cancel())
      const isChangedColumn = element.dataset.columnKey === changedColumnKey
      element.animate(
        [
          { transform: `translateX(${offsetX}px)`, opacity: isChangedColumn ? 0.82 : 1 },
          { transform: 'translateX(0)', opacity: 1 },
        ],
        {
          duration: isChangedColumn ? 260 : 190,
          easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
        },
      )
    })
  })
}

function applyPinnedColumns({ previousPositions = null, changedColumnKey = '' } = {}) {
  const orderedKeys = displayFieldOrder()
  const activePinnedKeys = orderedKeys.filter((key) => pinnedColumns.has(key))
  const lastPinnedKey = activePinnedKeys.at(-1)
  let leftOffset = 0

  reorderTableColumns(orderedKeys)

  orderedKeys.forEach((key) => {
    const header = tableHead.querySelector(`[data-column-key="${key}"]`)
    const cells = document.querySelectorAll(`#curation-rows td[data-column-key="${key}"]`)
    const pinButton = header?.querySelector('[data-pin-column]')
    const isPinned = pinnedColumns.has(key)
    const elements = header ? [header, ...cells] : [...cells]

    elements.forEach((element) => {
      element.classList.toggle('pinned-column', isPinned)
      element.classList.toggle('last-pinned-column', isPinned && key === lastPinnedKey)
      element.style.left = isPinned ? `${Math.round(leftOffset)}px` : ''
    })

    if (pinButton) {
      pinButton.classList.toggle('is-active', isPinned)
      pinButton.setAttribute('aria-pressed', String(isPinned))
      if (key !== 'figure') {
        const columnLabel = header.querySelector('.column-heading > span')?.textContent || key
        pinButton.setAttribute('aria-label', `${isPinned ? 'Unpin' : 'Pin'} ${columnLabel} column`)
        pinButton.title = `${isPinned ? 'Unpin' : 'Pin'} ${columnLabel} column`
      }
    }

    if (isPinned && header) {
      leftOffset += header.getBoundingClientRect().width
    }
  })

  const optionalPinCount = Math.max(0, pinnedColumns.size - 1)
  clearPinsButton.disabled = optionalPinCount === 0
  clearPinsButton.setAttribute('aria-label', optionalPinCount
    ? `Clear ${optionalPinCount} optional pinned ${optionalPinCount === 1 ? 'column' : 'columns'}`
    : 'No optional pinned columns to clear')
  clearPinsButton.title = optionalPinCount
    ? `Clear ${optionalPinCount} optional pinned ${optionalPinCount === 1 ? 'column' : 'columns'}; Figure will remain pinned`
    : 'No optional pinned columns to clear'

  animateColumnReorder(previousPositions, changedColumnKey)
}

function globalValidationSummary() {
  const items = records.flatMap((record) =>
    Object.values(record.fields).filter((item) => item.status !== 'neutral'))
  return items.reduce((summary, item) => {
    summary.total += 1
    summary[item.status] += 1
    return summary
  }, { total: 0, validated: 0, review: 0, error: 0 })
}

function updateGlobalSummary() {
  const summary = globalValidationSummary()
  summaryFieldTotal.textContent = String(summary.total)
  summaryValidatedCount.textContent = String(summary.validated)
  summaryReviewCount.textContent = String(summary.review)
  summaryErrorCount.textContent = String(summary.error)
}

function selectRecord(recordId, { navigatePdf = true } = {}) {
  const record = records.find((candidate) => candidate.id === recordId)
  if (!record) return

  selectedRecordId = recordId
  document.querySelectorAll('#curation-rows tr').forEach((row) => {
    row.classList.toggle('selected', row.dataset.recordId === recordId)
  })
  selectedLabel.textContent = record.label
  evidenceQuote.textContent = record.quote
  evidenceCaptionLabel.innerHTML = `<span class="evidence-pulse" aria-hidden="true"></span> Selected evidence · page ${record.page}`
  Object.assign(pdfHighlight.style, {
    top: `${record.highlight.top}%`,
    left: `${record.highlight.left}%`,
    width: `${record.highlight.width}%`,
    height: `${record.highlight.height}%`,
  })

  if (navigatePdf) {
    setPage(record.page)
  }
}

function evidenceContent(record, fieldKey, item) {
  const validationDetail = item.message ? `${item.message} · ` : ''
  return {
    title: `${item.label}: ${item.value}`,
    description: record.fieldEvidence?.[fieldKey] || record.quote,
    meta: `${validationDetail}Page ${record.page} · Current status: ${statusLabel(item.status)}`,
  }
}

function setActiveCell(element, recordId, fieldKey) {
  activeCell = { recordId, fieldKey, element }
}

function openEvidencePopover(actionButton, recordId, fieldKey) {
  closePopover()
  setActiveCell(actionButton, recordId, fieldKey)

  const record = records.find((candidate) => candidate.id === activeCell.recordId)
  const item = record.fields[activeCell.fieldKey]
  const content = evidenceContent(record, activeCell.fieldKey, item)
  const symbol = statusSymbol(item.status)

  actionButton.classList.add('open')
  popoverTitle.textContent = content.title
  popoverDescription.textContent = content.description
  popoverMeta.textContent = content.meta
  popoverStatusIcon.className = `status-dot ${item.status === 'neutral' ? 'context' : item.status}`
  popoverStatusIcon.textContent = item.status === 'neutral' ? 'i' : symbol
  popover.hidden = false

  requestAnimationFrame(() => positionPopover(actionButton))
}

function positionPopover(cellButton) {
  const panelRect = document.querySelector('.curation-panel').getBoundingClientRect()
  const cellRect = cellButton.getBoundingClientRect()
  const width = popover.offsetWidth
  const height = popover.offsetHeight
  const centered = cellRect.left - panelRect.left + cellRect.width / 2 - width / 2
  const left = Math.max(12, Math.min(centered, panelRect.width - width - 12))
  let top = cellRect.bottom - panelRect.top + 9

  if (top + height > panelRect.height - 48) {
    top = Math.max(12, cellRect.top - panelRect.top - height - 9)
  }

  const caretLeft = Math.max(18, Math.min(cellRect.left - panelRect.left + cellRect.width / 2 - left - 6, width - 30))
  popover.style.left = `${left}px`
  popover.style.top = `${top}px`
  popover.style.setProperty('--caret-left', `${caretLeft}px`)
}

function closePopover() {
  document.querySelectorAll('.cell-action-button.open').forEach((button) => button.classList.remove('open'))
  popover.hidden = true
}

function setPage(nextPage) {
  const numericPage = Number.parseInt(nextPage, 10)
  if (!Number.isFinite(numericPage)) return

  const previewPage = Math.max(1, Math.min(3, numericPage))
  if (numericPage > 3) {
    showToast('This standalone demo includes rendered previews for pages 1–3. The full 10-page PDF is available from the download button.')
  }

  currentPage = previewPage
  pageInput.value = String(currentPage)
  pdfPageImage.src = `./public/pdf-page-${String(currentPage).padStart(2, '0')}.jpg`
  pdfPageImage.alt = `Page ${currentPage} of the test publication Absolute Quantification of Proteins in the Eye of Drosophila melanogaster`
  pdfHighlight.classList.toggle('hidden', currentPage !== records.find((record) => record.id === selectedRecordId).page)
}

function setZoom(nextZoom) {
  currentZoom = Math.max(70, Math.min(160, nextZoom))
  zoomValue.value = `${currentZoom}%`
  zoomValue.textContent = `${currentZoom}%`
  pdfSheet.style.width = `${currentZoom}%`
}

function setFocusMode(nextFocus) {
  focusMode = nextFocus
  workspace.classList.toggle('focus-mode', focusMode)
  focusButton.setAttribute('aria-pressed', String(focusMode))
  focusButton.querySelector('span:last-child').textContent = focusMode ? 'Show PDF' : 'Focus grid'
  resizeHandle.setAttribute('aria-hidden', String(focusMode))
  closePopover()
}

function showToast(message) {
  window.clearTimeout(toastTimer)
  toast.textContent = message
  toast.classList.add('show')
  toastTimer = window.setTimeout(() => toast.classList.remove('show'), 3600)
}

function openEditDialog() {
  if (!activeCell) return
  const record = records.find((candidate) => candidate.id === activeCell.recordId)
  const item = record.fields[activeCell.fieldKey]
  editTitle.textContent = `Edit ${item.label}`
  editInput.value = item.value
  closePopover()
  editDialog.showModal()
  requestAnimationFrame(() => editInput.select())
}

rowContainer.addEventListener('click', (event) => {
  const validateButton = event.target.closest('[data-validate-row]')
  if (validateButton) {
    const record = records.find((candidate) => candidate.id === validateButton.dataset.validateRow)
    if (!record) return

    Object.values(record.fields).forEach((item) => {
      if (item.status === 'neutral') return
      item.status = 'validated'
      item.message = ''
    })
    selectedRecordId = record.id
    renderRecords()
    selectRecord(record.id, { navigatePdf: false })
    updateGlobalSummary()
    closePopover()
    showToast(`${record.label}: all curated fields validated. Figure and Notes were unchanged.`)
    return
  }

  const actionButton = event.target.closest('[data-cell-action]')
  const cell = event.target.closest('.field-cell')
  if (!cell) return

  const { recordId, fieldKey } = cell.dataset
  const record = records.find((candidate) => candidate.id === recordId)
  const item = record?.fields[fieldKey]
  if (!record || !item) return

  selectRecord(recordId, { navigatePdf: actionButton?.dataset.cellAction === 'evidence' })

  if (!actionButton) {
    closePopover()
    return
  }

  if (actionButton.dataset.cellAction === 'evidence') {
    pdfHighlight.classList.remove('hidden')
    openEvidencePopover(actionButton, recordId, fieldKey)
    return
  }

  if (actionButton.dataset.cellAction === 'toggle-validation') {
    const wasValidated = item.status === 'validated'
    item.status = wasValidated ? 'review' : 'validated'
    item.message = ''
    renderRecords()
    selectRecord(recordId, { navigatePdf: false })
    updateGlobalSummary()
    closePopover()
    showToast(`${item.label} marked ${wasValidated ? 'not validated and returned to curator review' : 'validated by the curator'}.`)
    return
  }

  if (actionButton.dataset.cellAction === 'edit') {
    closePopover()
    setActiveCell(actionButton, recordId, fieldKey)
    openEditDialog()
  }
})

tableHead.addEventListener('click', (event) => {
  const pinButton = event.target.closest('[data-pin-column]')
  if (!pinButton) return

  const columnKey = pinButton.dataset.pinColumn
  const columnLabel = pinButton.closest('th')?.querySelector('.column-heading > span')?.textContent || columnKey

  if (columnKey === 'figure') {
    showToast('Figure stays pinned so the source context is always visible.')
    return
  }

  const previousPositions = captureColumnPositions()

  if (pinnedColumns.has(columnKey)) {
    pinnedColumns.delete(columnKey)
  } else {
    pinnedColumns.add(columnKey)
  }

  applyPinnedColumns({ previousPositions, changedColumnKey: columnKey })
  pinButton.focus({ preventScroll: true })
  closePopover()
  showToast(pinnedColumns.has(columnKey)
    ? `${columnLabel} moved into the pinned group beside Figure.`
    : `${columnLabel} returned to its original table position.`)
})

tableHead.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return
  const pinButton = event.target.closest('[data-pin-column]')
  if (!pinButton) return
  event.preventDefault()
  pinButton.click()
})

clearPinsButton.addEventListener('click', () => {
  const optionalPinnedKeys = [...pinnedColumns].filter((key) => key !== 'figure')
  if (!optionalPinnedKeys.length) return

  const previousPositions = captureColumnPositions()
  pinnedColumns.clear()
  pinnedColumns.add('figure')
  applyPinnedColumns({ previousPositions })
  closePopover()
  showToast(`${optionalPinnedKeys.length} pinned ${optionalPinnedKeys.length === 1 ? 'column' : 'columns'} cleared. Figure remains pinned.`)
})

tableScroll.addEventListener('scroll', () => {
  if (!popover.hidden && activeCell?.element) positionPopover(activeCell.element)
})

tableScroll.addEventListener('wheel', (event) => {
  if (!event.shiftKey || Math.abs(event.deltaY) < Math.abs(event.deltaX)) return
  event.preventDefault()
  tableScroll.scrollLeft += event.deltaY
}, { passive: false })

document.querySelector('#popover-close').addEventListener('click', closePopover)

editForm.addEventListener('submit', (event) => {
  const action = event.submitter?.value
  if (action === 'cancel' || !activeCell) return
  event.preventDefault()
  const record = records.find((candidate) => candidate.id === activeCell.recordId)
  const item = record.fields[activeCell.fieldKey]
  const nextValue = editInput.value.trim()
  const validationRequired = item.status !== 'neutral'

  if (!nextValue) {
    editInput.setCustomValidity('Enter a value before saving.')
    editInput.reportValidity()
    return
  }

  editInput.setCustomValidity('')
  item.value = nextValue
  item.status = validationRequired ? 'review' : 'neutral'
  item.message = ''
  renderRecords()
  selectRecord(record.id, { navigatePdf: false })
  updateGlobalSummary()
  editDialog.close()
  showToast(validationRequired
    ? `${item.label} updated locally and marked as needing review.`
    : `${item.label} updated locally. Validation is not required for this context field.`)
})

pageInput.addEventListener('change', () => setPage(pageInput.value))
document.querySelector('#previous-page').addEventListener('click', () => setPage(currentPage - 1))
document.querySelector('#next-page').addEventListener('click', () => setPage(currentPage + 1))
document.querySelector('#zoom-out').addEventListener('click', () => setZoom(currentZoom - 10))
document.querySelector('#zoom-in').addEventListener('click', () => setZoom(currentZoom + 10))
document.querySelector('#toggle-highlight').addEventListener('click', () => pdfHighlight.classList.toggle('hidden'))
document.querySelector('#open-pdf').addEventListener('click', () => window.open('./public/sample-fly-publication.pdf', '_blank', 'noopener'))

focusButton.addEventListener('click', () => setFocusMode(!focusMode))
document.querySelector('.collapse-pdf').addEventListener('click', () => setFocusMode(true))

document.querySelector('#accept-validated').addEventListener('click', (event) => {
  const summary = globalValidationSummary()
  event.currentTarget.textContent = 'Validated accepted'
  showToast(`${summary.validated} validated curated fields accepted. ${summary.review} review items and ${summary.error} blocking item remain.`)
})

document.querySelector('#submit-record').addEventListener('click', (event) => {
  const selected = records.find((record) => record.id === selectedRecordId)
  event.currentTarget.textContent = 'Submitted'
  event.currentTarget.disabled = true
  showToast(`${selected.label} submitted to the mock queue. No live curation data was changed.`)
})

document.querySelector('#validation-summary').addEventListener('click', () => {
  summaryDrawer.classList.add('open')
  summaryDrawer.setAttribute('aria-hidden', 'false')
})

document.querySelector('#close-summary').addEventListener('click', () => {
  summaryDrawer.classList.remove('open')
  summaryDrawer.setAttribute('aria-hidden', 'true')
})

densityButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const density = button.dataset.density
    document.body.classList.toggle('comfortable-grid', density === 'comfortable')
    densityButtons.forEach((candidate) => {
      candidate.setAttribute('aria-pressed', String(candidate === button))
    })
    closePopover()
    showToast(`${density === 'comfortable' ? 'Comfortable' : 'Compact'} row density enabled.`)
  })
})

resizeHandle.addEventListener('pointerdown', (event) => {
  if (focusMode) return
  resizeHandle.setPointerCapture(event.pointerId)
  resizeHandle.classList.add('dragging')
})

resizeHandle.addEventListener('pointermove', (event) => {
  if (!resizeHandle.hasPointerCapture(event.pointerId)) return
  const rect = workspace.getBoundingClientRect()
  const percent = ((event.clientX - rect.left) / rect.width) * 100
  const constrained = Math.max(22, Math.min(58, percent))
  workspace.style.setProperty('--live-pdf-width', `${constrained}%`)
  resizeHandle.setAttribute('aria-valuenow', String(Math.round(constrained)))
  closePopover()
})

resizeHandle.addEventListener('pointerup', (event) => {
  if (resizeHandle.hasPointerCapture(event.pointerId)) {
    resizeHandle.releasePointerCapture(event.pointerId)
  }
  resizeHandle.classList.remove('dragging')
})

resizeHandle.addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
  event.preventDefault()
  const current = Number.parseInt(resizeHandle.getAttribute('aria-valuenow'), 10) || 34
  const next = Math.max(22, Math.min(58, current + (event.key === 'ArrowRight' ? 2 : -2)))
  workspace.style.setProperty('--live-pdf-width', `${next}%`)
  resizeHandle.setAttribute('aria-valuenow', String(next))
})

document.addEventListener('click', (event) => {
  if (popover.hidden) return
  if (popover.contains(event.target) || event.target.closest('[data-cell-action="evidence"]')) return
  closePopover()
})

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return
  closePopover()
  summaryDrawer.classList.remove('open')
  summaryDrawer.setAttribute('aria-hidden', 'true')
})

window.addEventListener('resize', () => {
  applyPinnedColumns()
  if (!popover.hidden && activeCell?.element) positionPopover(activeCell.element)
})

renderRecords()
selectRecord(records[0].id)
updateGlobalSummary()
setZoom(100)
