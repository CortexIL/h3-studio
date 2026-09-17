import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'

import type { AdminActivity } from '@/api/types'
import { makeActivity } from '@/mocks/data'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ActivityTab } from './ActivityTab'

function serve(activity: AdminActivity) {
  server.use(
    http.get('/api/me', () => HttpResponse.json({ id: 'u-admin', email: 'you@h3.local', role: 'admin' })),
    http.get('/api/admin/activity', () => HttpResponse.json(activity)),
  )
}

beforeEach(() => serve(makeActivity()))

test('a GPU with work says so, with the queue behind it', async () => {
  renderWithProviders(<ActivityTab />)
  expect(await screen.findByText('A GPU is up, with work to do')).toBeInTheDocument()
  expect(screen.getByText('3 waiting · 1 rendering')).toBeInTheDocument()
})

test('a GPU up with nothing to render, and nobody there, is called out', async () => {
  const base = makeActivity()
  serve({
    ...base,
    people: base.people.map((p) => ({ ...p, here: false, queued: 0, running: 0 })),
    here_count: 0,
    queued: 0,
    running: 0,
    verdict: 'idle',
    quiet_for_s: 2_040,
    session: { ...base.session, cost_usd: 0.46 },
  })
  renderWithProviders(<ActivityTab />)
  const alert = await screen.findByRole('alert')
  expect(within(alert).getByText('A GPU is up with nothing to render')).toBeInTheDocument()
  // The three facts an admin needs before deciding it was left on.
  expect(alert).toHaveTextContent('nobody has the app open')
  expect(alert).toHaveTextContent('nothing has finished for 34m 0s')
  expect(alert).toHaveTextContent('$0.46 this session')
})

test('nothing running is not an alarm', async () => {
  const base = makeActivity()
  serve({ ...base, verdict: 'off', pods_up: 0, queued: 0, running: 0 })
  renderWithProviders(<ActivityTab />)
  expect(await screen.findByText('No GPU is running')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('each person says whether they are here and for how long', async () => {
  renderWithProviders(<ActivityTab />)
  const dana = (await screen.findByText('dana@h3.local')).closest('tr')!
  expect(within(dana).getByText('Here now')).toBeInTheDocument()
  expect(within(dana).getByText('53m 0s')).toBeInTheDocument()     // using_for_s 3180
  expect(within(dana).getByText('40m 0s ago')).toBeInTheDocument() // acted_s_ago 2400

  // Away: the time since they were last served, not a stretch still counting up.
  const omer = screen.getByText('omer@h3.local').closest('tr')!
  expect(within(omer).getByText('1h 30m ago')).toBeInTheDocument()
  expect(within(omer).queryByText('Here now')).not.toBeInTheDocument()
})

test('someone who has never signed in says so rather than showing a zero', async () => {
  renderWithProviders(<ActivityTab />)
  const guest = (await screen.findByText('guest@h3.local')).closest('tr')!
  expect(within(guest).getByText('Never')).toBeInTheDocument()
  expect(within(guest).getAllByText('—').length).toBeGreaterThan(0)
})
