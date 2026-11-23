import { describe, expect, it } from 'vitest'

import { render, screen } from '@/test/test-utils'
import type { EvidenceRecord } from '@/features/curation/types'

import EvidenceQuote from './EvidenceQuote'

const pageLessSectionLess: EvidenceRecord = {
  entity: 'XS0401',
  verified_quote: 'Strain XS0401 was kept at 25 C.',
  page: null,
  section: null,
  chunk_id: 'chunk-36',
  file_id: 'aaaaaaaa-0000-0000-0000-000000000002',
  file_name: 'Table_S1.xlsx',
}

describe('EvidenceQuote location label', () => {
  it.each([true, false])('shows no location at all for evidence with no page and no section (interactive %s)', (interactive) => {
    const { container } = render(
      <EvidenceQuote evidenceRecord={pageLessSectionLess} borderColor="#000" interactive={interactive} />,
    )

    expect(screen.getByText(/Strain XS0401 was kept at 25 C\./)).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/null|None|Unknown|Location unavailable|p\. /)
  })

  it('shows only the page when the section is null', () => {
    render(<EvidenceQuote evidenceRecord={{ ...pageLessSectionLess, page: 4 }} borderColor="#000" interactive={false} />)

    expect(screen.getByText('p. 4')).toBeInTheDocument()
  })
})
