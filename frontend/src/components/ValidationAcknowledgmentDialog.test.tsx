import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@/test/test-utils'
import userEvent from '@testing-library/user-event'
import ValidationAcknowledgmentDialog from './ValidationAcknowledgmentDialog'
import { fetchWithValidationAcknowledgment } from '@/services/validationAcknowledgment'

const scope = { configuration_fingerprint: 'sha256:' + 'a'.repeat(64), data_type: 'Allele identity',
  unvalidated_fields: [{ path: 'attributes.id', label: 'Database ID' }], disabled_checks: [], status: 'not_database_validated' }
const required = () => new Response(JSON.stringify({ detail: { code: 'validation_acknowledgment_required', scopes: [scope] } }), { status: 409 })
afterEach(() => vi.unstubAllGlobals())

describe('extraction-only acknowledgment', () => {
  it('requires an unchecked human choice and retries the exact request only after recording it', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn().mockResolvedValueOnce(required()).mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    render(<ValidationAcknowledgmentDialog />)
    const init = { method: 'PUT', body: '{"exact":"draft"}' }
    const result = fetchWithValidationAcknowledgment('/api/flows/one', init)
    expect(await screen.findByRole('heading', { name: 'Continue without database validation?' })).toBeInTheDocument()
    expect(screen.getByText('Database ID')).toBeInTheDocument()
    const checkbox = screen.getByRole('checkbox')
    expect(checkbox).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Continue without database validation' })).toBeDisabled()
    await user.click(checkbox)
    await user.click(screen.getByRole('button', { name: 'Continue without database validation' }))
    expect((await result).ok).toBe(true)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/validation-acknowledgments', expect.objectContaining({ body: JSON.stringify({ acknowledge_extraction_only: true, scopes: [scope] }) }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/flows/one', init)
  })

  it('Cancel never records consent or retries the rejected save', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn().mockResolvedValueOnce(required())
    vi.stubGlobal('fetch', fetchMock)
    render(<ValidationAcknowledgmentDialog />)
    const result = fetchWithValidationAcknowledgment('/api/flows/one').catch(error => error)
    await user.click(await screen.findByRole('button', { name: 'Cancel and configure validation' }))
    expect((await result).name).toBe('AbortError')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('aborting a pending launch closes the dialog without consent', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(required())
    vi.stubGlobal('fetch', fetchMock)
    render(<ValidationAcknowledgmentDialog />)
    const controller = new AbortController()
    const result = fetchWithValidationAcknowledgment('/api/chat/execute-flow', { signal: controller.signal }).catch(error => error)
    await screen.findByRole('checkbox')
    controller.abort()
    expect((await result).name).toBe('AbortError')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
