import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'

import { makeAdminStatus } from '@/mocks/data'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { GpuTab } from './GpuTab'

let policies: string[] = []
let budgets: number[] = []

beforeEach(() => {
  policies = []
  budgets = []
  server.use(
    http.get('/api/admin/status', () => HttpResponse.json(makeAdminStatus())),
    http.get('/api/admin/key-state', () => HttpResponse.json({ present: true, hint: '4f2a' })),
    http.post('/api/admin/policy', async ({ request }) => {
      const { policy } = (await request.json()) as { policy: string }
      policies.push(policy)
      return HttpResponse.json({ policy })
    }),
    http.post('/api/admin/budget', async ({ request }) => {
      const { session_limit_usd } = (await request.json()) as { session_limit_usd: number }
      budgets.push(session_limit_usd)
      return HttpResponse.json({ session_limit_usd })
    }),
  )
})

test('turning the GPU off asks first', async () => {
  const user = userEvent.setup()
  renderWithProviders(<GpuTab />)
  await user.click(await screen.findByRole('radio', { name: 'Off' }))
  const dialog = await screen.findByRole('alertdialog', { name: 'Turn the GPU off?' })
  expect(policies).toEqual([])
  await user.click(within(dialog).getByRole('button', { name: 'Turn off' }))
  await waitFor(() => expect(policies).toEqual(['off']))
})

test('the budget is checked before it is sent, and Enter saves it', async () => {
  const user = userEvent.setup()
  renderWithProviders(<GpuTab />)
  const input = await screen.findByLabelText('Limit (USD)')
  await user.clear(input)
  await user.type(input, '0')
  expect(screen.getByText('It has to be more than $0.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  await user.clear(input)
  await user.type(input, '12{Enter}')
  await waitFor(() => expect(budgets).toEqual([12]))
})

test('only the last four characters of the key are shown', async () => {
  renderWithProviders(<GpuTab />)
  expect(await screen.findByText('…4f2a')).toBeInTheDocument()
})

test('the faster-attention switch posts its new state', async () => {
  let posted: unknown = null
  server.use(
    http.post('/api/admin/sage', async ({ request }) => {
      posted = await request.json()
      return HttpResponse.json({ sage: true })
    }),
  )
  renderWithProviders(<GpuTab />)
  const sw = await screen.findByRole('switch', { name: 'Faster attention' })
  expect(sw).toHaveAttribute('aria-checked', 'false')
  await userEvent.click(sw)
  await waitFor(() => expect(posted).toEqual({ enabled: true }))
})
