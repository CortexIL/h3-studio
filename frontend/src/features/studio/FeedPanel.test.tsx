import { fireEvent, screen, waitFor, within } from '@testing-library/react'
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
  await screen.findAllByText('Making') // the status badge

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

/** jsdom has no layout; give every card a 100px row so the pointer maths has something to read. */
function layoutRows(ids: string[]) {
  return vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const id = (this as HTMLElement).dataset?.jobId
    const i = id ? ids.indexOf(id) : -1
    const top = i < 0 ? 0 : i * 100
    return { x: 0, y: top, top, bottom: top + 90, left: 0, right: 600, width: 600, height: 90, toJSON() {} } as DOMRect
  })
}
const handle = (text: string) => screen.getByRole('button', { name: `Reorder ${text}` })
const card = (text: string) => screen.getByText(text).closest('[data-job-id]') as HTMLElement

function captureOrder() {
  const sent: string[][] = []
  server.use(
    http.post('/api/jobs/order', async ({ request }) => {
      const body = (await request.json()) as { ids: string[] }
      sent.push(body.ids)
      return HttpResponse.json({ ok: true, reordered: body.ids.length })
    }),
  )
  return sent
}

const threeWaiting = () => [
  makeJob({ id: 'q-new', status: 'queued', prompt: 'newest waiting' }),
  makeJob({ id: 'q-mid', status: 'queued', prompt: 'middle waiting' }),
  makeJob({ id: 'q-old', status: 'queued', prompt: 'oldest waiting' }),
]

test('dragging a waiting clip by its handle sends the queue order, bottom first', async () => {
  // The mock server honours the order it was sent, as the real one does.
  const sent: string[][] = []
  serveJobs(() => threeWaiting().map((j) => ({ ...j, queue_position: sent.length ? sent[sent.length - 1]!.indexOf(j.id) : null })))
  server.use(
    http.post('/api/jobs/order', async ({ request }) => {
      const body = (await request.json()) as { ids: string[] }
      sent.push(body.ids)
      return HttpResponse.json({ ok: true, reordered: body.ids.length })
    }),
  )
  renderWithProviders(<FeedPanel />)
  await screen.findByText('newest waiting')
  const rows = layoutRows(['q-new', 'q-mid', 'q-old'])
  try {
    fireEvent.pointerDown(handle('oldest waiting'), { clientY: 245, button: 0, pointerId: 1 })
    fireEvent.pointerMove(window, { clientY: 10, pointerId: 1 })
    fireEvent.pointerUp(window, { clientY: 10, pointerId: 1 })
  } finally {
    rows.mockRestore()
  }
  // The list shows newest first; the queue renders the bottom entry next.
  await waitFor(() => expect(sent).toHaveLength(1))
  expect(sent[0]).toEqual(['q-mid', 'q-new', 'q-old'])
  // ... and the card has moved on screen without waiting for the next poll.
  const shown = () => [...document.querySelectorAll<HTMLElement>('[data-job-id]')].map((el) => el.dataset.jobId)
  await waitFor(() => expect(shown()).toEqual(['q-old', 'q-new', 'q-mid']))
})

test('waiting clips are listed in the order they will be made, next up at the bottom', async () => {
  serveJobs(() => [
    makeJob({ id: 'q-a', status: 'queued', prompt: 'made second', queue_position: 1 }),
    makeJob({ id: 'q-b', status: 'queued', prompt: 'made last', queue_position: 2 }),
    makeJob({ id: 'r-1', status: 'running', started_at: 1_700_000_000, prompt: 'being made' }),
    makeJob({ id: 'q-c', status: 'queued', prompt: 'made next', queue_position: 0 }),
  ])
  renderWithProviders(<FeedPanel />)
  await screen.findByText('made next')
  const shown = [...document.querySelectorAll<HTMLElement>('[data-job-id]')].map((el) => el.dataset.jobId)
  expect(shown).toEqual(['q-b', 'q-a', 'q-c', 'r-1'])
})

test('releasing below every card puts the clip last', async () => {
  serveJobs(threeWaiting)
  const sent = captureOrder()
  renderWithProviders(<FeedPanel />)
  await screen.findByText('newest waiting')
  const rows = layoutRows(['q-new', 'q-mid', 'q-old'])
  try {
    fireEvent.pointerDown(handle('newest waiting'), { clientY: 45, button: 0, pointerId: 1 })
    fireEvent.pointerMove(window, { clientY: 500, pointerId: 1 })
    expect(card('oldest waiting').className).toContain('after:bg-primary')
    fireEvent.pointerUp(window, { clientY: 500, pointerId: 1 })
  } finally {
    rows.mockRestore()
  }
  await waitFor(() => expect(sent).toHaveLength(1))
  expect(sent[0]).toEqual(['q-new', 'q-old', 'q-mid'])
})

test('finished clips have no handle', async () => {
  serveJobs(() => [
    makeJob({ id: 'd-1', status: 'done', video_url: '/api/video/d-1', prompt: 'finished one' }),
    makeJob({ id: 'q-1', status: 'queued', prompt: 'waiting one' }),
  ])
  renderWithProviders(<FeedPanel />)
  await screen.findByText('finished one')
  expect(screen.queryByRole('button', { name: 'Reorder finished one' })).toBeNull()
  expect(handle('waiting one')).toBeInTheDocument()
})

test('the card the pointer is over shows where the clip would land, and Escape cancels', async () => {
  serveJobs(() => [
    makeJob({ id: 'q-new', status: 'queued', prompt: 'newest waiting' }),
    makeJob({ id: 'q-old', status: 'queued', prompt: 'oldest waiting' }),
  ])
  renderWithProviders(<FeedPanel />)
  await screen.findByText('newest waiting')
  const rows = layoutRows(['q-new', 'q-old'])
  try {
    expect(document.querySelectorAll('[data-drop-target]')).toHaveLength(0)
    fireEvent.pointerDown(handle('oldest waiting'), { clientY: 145, button: 0, pointerId: 1 })
    fireEvent.pointerMove(window, { clientY: 10, pointerId: 1 })
    expect(card('newest waiting')).toHaveAttribute('data-drop-target')
    // never on the card being dragged itself
    expect(card('oldest waiting')).not.toHaveAttribute('data-drop-target')
    expect(card('oldest waiting').className).toContain('opacity-40')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(document.querySelectorAll('[data-drop-target]')).toHaveLength(0)
    expect(card('oldest waiting').className).not.toContain('opacity-40')
    // a release after the cancel must not reorder anything (no handler is registered: a request would fail the test)
    fireEvent.pointerUp(window, { clientY: 10, pointerId: 1 })
  } finally {
    rows.mockRestore()
  }
})

test('the arrow keys on the handle move a waiting clip without a drag', async () => {
  serveJobs(threeWaiting)
  const sent = captureOrder()
  renderWithProviders(<FeedPanel />)
  await screen.findByText('newest waiting')
  fireEvent.keyDown(handle('oldest waiting'), { key: 'ArrowUp' })
  await waitFor(() => expect(sent).toHaveLength(1))
  expect(sent[0]).toEqual(['q-mid', 'q-old', 'q-new'])
})

test('an extension shows the clip it continues as a video, not a broken image', async () => {
  serveJobs(() => [
    makeJob({
      id: 'ext-1',
      status: 'queued',
      mode: 'extend',
      prompt: 'keeps going',
      ref_images: ['uploads/u1/tail.mp4', 'uploads/u1/arrive.png'],
    }),
  ])
  renderWithProviders(<FeedPanel />)
  const card = (await screen.findByText('keeps going')).closest('article')!
  // the backdrop and the strip both used to be <img> tags pointing at an mp4
  expect(card.querySelectorAll('video[src^="/api/image/uploads/u1/tail.mp4"]')).toHaveLength(2)
  expect(card.querySelector('img[src^="/api/image/uploads/u1/tail.mp4"]')).toBeNull()
  expect(card.querySelector('img[src="/api/image/uploads/u1/arrive.png"]')).not.toBeNull()
})


test('a finished clip can be sent for an upscale from its menu', async () => {
  const calls: { id: string; deliver: string }[] = []
  serveJobs(() => [makeJob({ id: 'done-9', status: 'done', video_url: '/api/video/done-9', prompt: 'the finished one' })])
  server.use(
    http.post('/api/jobs/:id/upscale', async ({ params, request }) => {
      const body = (await request.json()) as { deliver: string }
      calls.push({ id: String(params.id), deliver: body.deliver })
      return HttpResponse.json({ ok: true, job_id: 'up-1', frames: 124 })
    }),
  )
  const user = userEvent.setup()
  renderWithProviders(<FeedPanel />)
  const card = (await screen.findByText('the finished one')).closest('article')!
  await user.click(within(card).getByRole('button', { name: /more/i }))
  await user.click(await screen.findByRole('menuitem', { name: 'Enlarge to 1080p' }))
  await waitFor(() => expect(calls).toEqual([{ id: 'done-9', deliver: '1080p' }]))
})

test('an upscale job shows no reference strip and reads as an upscale', async () => {
  serveJobs(() => [makeJob({ id: 'up-2', status: 'queued', mode: 'upscale', prompt: 'Upscale ×2 · the finished one', source_job_id: 'done-9' })])
  renderWithProviders(<FeedPanel />)
  const card = (await screen.findByText('Upscale ×2 · the finished one')).closest('article')!
  expect(within(card).getAllByText('Enlarged').length).toBeGreaterThan(0)
  expect(card.querySelector('img[src^="/api/image/"]')).toBeNull()
})
