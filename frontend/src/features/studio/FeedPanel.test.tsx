import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { expect, test, vi } from 'vitest'

import type { Job } from '@/api/types'
import { makeJob } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { FeedPanel } from './FeedPanel'

function serveJobs(get: () => Job[]) {
  server.use(http.get('/api/jobs', () => HttpResponse.json({ jobs: get() })))
}

test('a poll that changes another job leaves a playing video alone', async () => {
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  const done = makeJob({ id: 'done-1', status: 'done', video_url: '/api/video/done-1', prompt: 'the finished one' })
  let other = makeJob({ id: 'q-1', status: 'queued', prompt: 'the waiting one' })
  serveJobs(() => [other, done])

  const { client } = renderWithProviders(<FeedPanel />)
  const before = await screen.findByLabelText(/Clip: the finished one/)

  // Another job moves on; the server sends a fresh list.
  other = { ...other, status: 'running', started_at: 1_700_000_100 }
  await client.invalidateQueries({ queryKey: ['jobs'] })
  await screen.findAllByText('Generating') // the badge and the stage both say it

  const after = screen.getByLabelText(/Clip: the finished one/)
  expect(after).toBe(before) // same DOM node: never remounted
  expect(load).not.toHaveBeenCalled()
})

test('cancelling a running job asks first; a queued one does not', async () => {
  const cancels: string[] = []
  serveJobs(() => [
    makeJob({ id: 'run-1', status: 'running', started_at: 1_700_000_000, prompt: 'running one' }),
    makeJob({ id: 'q-2', status: 'queued', prompt: 'queued one' }),
  ])
  server.use(
    http.post('/api/jobs/:id/cancel', ({ params }) => {
      cancels.push(String(params.id))
      return HttpResponse.json({ ok: true })
    }),
  )
  const user = userEvent.setup()
  renderWithProviders(<FeedPanel />)

  const queuedCard = (await screen.findByText('queued one')).closest('article')!
  await user.click(within(queuedCard).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(cancels).toEqual(['q-2']))

  const runningCard = screen.getByText('running one').closest('article')!
  await user.click(within(runningCard).getByRole('button', { name: 'Cancel' }))
  expect(await screen.findByRole('alertdialog', { name: 'Cancel this clip?' })).toBeInTheDocument()
  expect(cancels).toEqual(['q-2'])
  await user.click(screen.getByRole('button', { name: 'Cancel clip' }))
  await waitFor(() => expect(cancels).toEqual(['q-2', 'run-1']))
})

test('clearing finished jobs explains the archive keeps the clips', async () => {
  serveJobs(() => [makeJob({ status: 'done', video_url: '/api/video/x', prompt: 'done one' })])
  const user = userEvent.setup()
  renderWithProviders(<FeedPanel />)
  await screen.findByText('done one')
  await user.click(screen.getByRole('button', { name: /Clear finished/ }))
  const dialog = await screen.findByRole('alertdialog', { name: 'Clear finished jobs?' })
  expect(dialog).toHaveTextContent('stay in your Archive')
})

test('an empty feed says what to do next', async () => {
  serveJobs(() => [])
  renderWithProviders(<FeedPanel />)
  expect(await screen.findByText('Nothing here yet')).toBeInTheDocument()
})

test('a prompt that looks like markup is shown as text', async () => {
  serveJobs(() => [makeJob({ prompt: '<img src=x onerror=alert(1)>' })])
  renderWithProviders(<FeedPanel />)
  expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
  expect(document.querySelector('img[src="x"]')).toBeNull()
})
