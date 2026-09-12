import { render, screen } from '@/test/test-utils'
import { describe, it, expect } from 'vitest'
import { ChatMarkdown } from './ChatMarkdown'

describe('ChatMarkdown', () => {
  it('renders emphasis and readable lists and tables', () => {
    render(<ChatMarkdown>{'**Alleles**\n\n- Name\n- Identifier\n\n| Name | ID |\n| --- | --- |\n| Example | 123 |'}</ChatMarkdown>)
    expect(screen.getByText('Alleles').tagName).toBe('STRONG')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
  it('does not execute HTML or render remote images or javascript links', () => {
    const { container } = render(<ChatMarkdown>{'<script>alert(1)</script>\n\n![remote](https://example.org/image.png)\n\n[unsafe](javascript:alert%281%29)'}</ChatMarkdown>)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('unsafe').getAttribute('href')).not.toMatch(/^javascript:/)
  })
})
