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

test('choosing how many GPUs may run at once saves it', async () => {
  const sent: number[] = []
  server.use(
    http.post('/api/admin/max-pods', async ({ request }) => {
      const { max_pods } = (await request.json()) as { max_pods: number }
      sent.push(max_pods)
      return HttpResponse.json({ max_pods })
    }),
  )
  const user = userEvent.setup()
  renderWithProviders(<GpuTab />)
  const group = await screen.findByRole('radiogroup', { name: 'How many GPUs may run at once' })
  expect(within(group).getAllByRole('radio')).toHaveLength(5)
  await user.click(within(group).getByRole('radio', { name: '3' }))
  await waitFor(() => expect(sent).toEqual([3]))
})

test('every running GPU is listed when there are several', async () => {
  const status = makeAdminStatus()
  status.max_pods = 2
  const [first] = status.pods
  if (!first) throw new Error('the sample status has a pod')
  status.pods = [first, { ...first, number: 2, pod_id: 'pod-2', rendering: 0, cost_usd: 0.1 }]
  server.use(http.get('/api/admin/status', () => HttpResponse.json(status)))
  renderWithProviders(<GpuTab />)
  const list = await screen.findByRole('list', { name: 'GPUs at once' })
  expect(within(list).getByText('GPU 2')).toBeInTheDocument()
  expect(within(list).getByText('Waiting for work')).toBeInTheDocument()
  expect(within(list).getByText('Making 1')).toBeInTheDocument()
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


test('one GPU can be stopped from its own row, once confirmed', async () => {
  const status = makeAdminStatus()
  status.max_pods = 2
  const [first] = status.pods
  if (!first) throw new Error('the sample status has a pod')
  status.pods = [first, { ...first, number: 2, pod_id: 'pod-2', rendering: 0, cost_usd: 0.1 }]
  const stopped: number[] = []
  server.use(
    http.get('/api/admin/status', () => HttpResponse.json(status)),
    http.post('/api/admin/pods/:number/stop', ({ params }) => {
      stopped.push(Number(params.number))
      return HttpResponse.json({ ok: true })
    }),
  )
  const user = userEvent.setup()
  renderWithProviders(<GpuTab />)

  const list = await screen.findByRole('list', { name: 'GPUs at once' })
  await user.click(within(list).getByRole('button', { name: 'Stop GPU 2' }))

  // Terminating a pod costs whatever it has downloaded, so it asks first.
  const dialog = await screen.findByRole('alertdialog', { name: 'Stop GPU 2?' })
  expect(stopped).toEqual([])
  await user.click(within(dialog).getByRole('button', { name: 'Stop GPU 2' }))
  await waitFor(() => expect(stopped).toEqual([2]))
})

test('stopping a GPU that is rendering says what happens to the clips', async () => {
  const user = userEvent.setup()
  renderWithProviders(<GpuTab />)
  // The sample status has one pod, rendering one clip: its button is in the header.
  await user.click(await screen.findByRole('button', { name: 'Stop GPU 1' }))
  const dialog = await screen.findByRole('alertdialog', { name: 'Stop GPU 1?' })
  expect(dialog).toHaveTextContent('go back to the queue')
})

