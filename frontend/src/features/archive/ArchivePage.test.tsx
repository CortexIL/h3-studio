import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'

import type { Clip } from '@/api/types'
import { makeStatus } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ArchivePage } from './ArchivePage'

function clip(id: string, prompt: string): Clip {
  return {
    id, prompt, ref_images: [], seconds: 10, seed: null, preset: 'final', mode: 'i2v',
    keep_audio: true, effects: [],
  sound: null,
  music: null,
  steps: null,
  shift_video: null,
  shift_audio: null,
  width: null,
  height: null,
  keyframes: [],
  audio: null,
  ref_videos: [],
  ref_audios: [],
  source_job_id: null,
  upscale_factor: null,
    created_at: 1_700_000_000, finished_at: 1_700_000_100, bytes: 2_000_000,
    video_url: `/api/video/${id}`, poster_url: `/api/poster/${id}`,
  }
}

let clips: Clip[] = []
let queries: string[] = []

beforeEach(() => {
  clips = [clip('a', 'a red car at dusk'), clip('b', 'a paper boat in the rain')]
  queries = []
  server.use(
    http.get('/api/status', () => HttpResponse.json(makeStatus())),
    http.get('/api/archive', ({ request }) => {
      const q = new URL(request.url).searchParams.get('q') ?? ''
      queries.push(q)
      return HttpResponse.json({ clips: clips.filter((c) => c.prompt.includes(q)), next_cursor: null })
    }),
  )
})

test('search waits for a pause, then asks the server', async () => {
  const user = userEvent.setup()
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  expect(await screen.findByText('a red car at dusk')).toBeInTheDocument()
  await user.type(screen.getByLabelText('Search your prompts'), 'boat')
  await waitFor(() => expect(screen.queryByText('a red car at dusk')).not.toBeInTheDocument())
  expect(screen.getByText('a paper boat in the rain')).toBeInTheDocument()
  // One request for the page, one after the pause - not one per keystroke.
  expect(queries).toEqual(['', 'boat'])
  expect(screen.getByText(/1 clip matches/)).toBeInTheDocument()
})

test('a search with no results offers to clear it', async () => {
  const user = userEvent.setup()
  renderWithProviders(<ArchivePage />, { route: '/archive?q=zebra' })
  expect(await screen.findByText('No clips match')).toBeInTheDocument()
  await user.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]!)
  expect(await screen.findByText('a red car at dusk')).toBeInTheDocument()
  expect(screen.getByLabelText('Search your prompts')).toHaveValue('')
})

test('an empty archive points to the Studio', async () => {
  clips = []
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  expect(await screen.findByText('No clips yet')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Go to the Studio' })).toHaveAttribute('href', '/')
})

test('deleting a clip asks first, then removes it', async () => {
  const deleted: string[] = []
  server.use(
    http.delete('/api/archive/:id', ({ params }) => {
      deleted.push(String(params.id))
      clips = clips.filter((c) => c.id !== params.id)
      return HttpResponse.json({ ok: true })
    }),
  )
  const user = userEvent.setup()
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  const card = (await screen.findByText('a red car at dusk')).closest('article')!
  await user.click(within(card).getByRole('button', { name: 'Clip actions' }))
  await user.click(await screen.findByRole('menuitem', { name: /Delete clip/ }))
  const dialog = await screen.findByRole('alertdialog', { name: 'Delete this clip?' })
  expect(deleted).toEqual([])
  await user.click(within(dialog).getByRole('button', { name: 'Delete clip' }))
  await waitFor(() => expect(deleted).toEqual(['a']))
  await waitFor(() => expect(screen.queryByText('a red car at dusk')).not.toBeInTheDocument())
})

// ---- picking several clips ----

test('picking clips shows a count and downloads them as one zip', async () => {
  const user = userEvent.setup()
  const hrefs: string[] = []
  const realClick = HTMLAnchorElement.prototype.click
  HTMLAnchorElement.prototype.click = function () {
    hrefs.push(this.getAttribute('href') ?? '')
  }
  try {
    renderWithProviders(<ArchivePage />, { route: '/archive' })
    await screen.findByText('a red car at dusk')

    await user.click(screen.getByRole('button', { name: /^Select: a red car/ }))
    await user.click(screen.getByRole('button', { name: /^Select: a paper boat/ }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Download 2' }))
    expect(hrefs).toEqual(['/api/archive/download?ids=a,b'])
  } finally {
    HTMLAnchorElement.prototype.click = realClick
  }
})

test('a picked clip can be unpicked, and clearing drops them all', async () => {
  const user = userEvent.setup()
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')

  await user.click(screen.getByRole('button', { name: /^Select: a red car/ }))
  await user.click(screen.getByRole('button', { name: /^Deselect: a red car/ }))
  expect(screen.queryByText(/selected/)).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /^Select: a red car/ }))
  await user.click(screen.getByRole('button', { name: 'Clear' }))
  expect(screen.queryByText(/selected/)).not.toBeInTheDocument()
})

test('holding a modifier picks a clip instead of opening it', async () => {
  const user = userEvent.setup()
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')

  await user.keyboard('{Meta>}')
  await user.click(screen.getByRole('button', { name: /^Open: a red car/ }))
  await user.keyboard('{/Meta}')

  expect(screen.getByText('1 selected')).toBeInTheDocument()
  // and the viewer did not open over the top of it
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('a clip that has lost its file is left out of the download', async () => {
  const user = userEvent.setup()
  clips = [clip('a', 'a red car at dusk'), { ...clip('b', 'a paper boat in the rain'), video_url: null }]
  const hrefs: string[] = []
  const realClick = HTMLAnchorElement.prototype.click
  HTMLAnchorElement.prototype.click = function () {
    hrefs.push(this.getAttribute('href') ?? '')
  }
  try {
    renderWithProviders(<ArchivePage />, { route: '/archive' })
    await screen.findByText('a red car at dusk')
    await user.click(screen.getByRole('button', { name: /^Select: a red car/ }))
    await user.click(screen.getByRole('button', { name: /^Select: a paper boat/ }))
    await user.click(screen.getByRole('button', { name: 'Download 2' }))
    expect(hrefs).toEqual(['/api/archive/download?ids=a'])
  } finally {
    HTMLAnchorElement.prototype.click = realClick
  }
})

test('a selection can be re-run, once the cost is confirmed', async () => {
  const user = userEvent.setup()
  let sent: { ids: string[] } | null = null
  server.use(
    http.post('/api/jobs/again', async ({ request }) => {
      sent = (await request.json()) as { ids: string[] }
      return HttpResponse.json({ queued: sent.ids.length, created: sent.ids })
    }),
  )
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')

  await user.click(screen.getByRole('button', { name: /^Select: a red car/ }))
  await user.click(screen.getByRole('button', { name: /^Select: a paper boat/ }))
  await user.click(screen.getByRole('button', { name: /Run again/ }))

  // Spending money always asks first.
  const dialog = await screen.findByRole('alertdialog', { name: 'Run 2 clips again?' })
  expect(dialog).toHaveTextContent('charged as a new clip')
  expect(sent).toBeNull()

  await user.click(within(dialog).getByRole('button', { name: 'Run 2 again' }))
  await waitFor(() => expect(sent).not.toBeNull())
  expect(sent!.ids).toEqual(['a', 'b'])
  // the selection is done with once it has been acted on
  await waitFor(() => expect(screen.queryByText(/selected/)).not.toBeInTheDocument())
})

test('the archive says a sweep is possible before anything is picked', async () => {
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')
  expect(screen.getByText(/Drag across the grid to pick several/)).toBeInTheDocument()
})

test('a selection can be deleted in one go, once it is confirmed', async () => {
  const user = userEvent.setup()
  let sent: string[][] = []
  server.use(
    http.post('/api/archive/delete', async ({ request }) => {
      const { ids } = (await request.json()) as { ids: string[] }
      sent.push(ids)
      clips = clips.filter((c) => !ids.includes(c.id))
      return HttpResponse.json({ deleted: ids.length, kept: 0 })
    }),
  )
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')

  await user.click(screen.getByRole('button', { name: /^Select: a red car/ }))
  await user.click(screen.getByRole('button', { name: /^Select: a paper boat/ }))
  await user.click(screen.getByRole('button', { name: 'Delete 2' }))

  // Deleting for good always asks first.
  const dialog = await screen.findByRole('alertdialog', { name: 'Delete 2 clips?' })
  expect(dialog).toHaveTextContent('deleted from storage')
  expect(sent).toEqual([])

  await user.click(within(dialog).getByRole('button', { name: 'Delete 2' }))
  await waitFor(() => expect(sent).toEqual([['a', 'b']]))
  await waitFor(() => expect(screen.queryByText('a red car at dusk')).not.toBeInTheDocument())
  expect(screen.queryByText(/selected/)).not.toBeInTheDocument()
})

test('a selection larger than one request is sent in whole batches', async () => {
  const user = userEvent.setup()
  clips = Array.from({ length: 201 }, (_, i) => clip(`c${i}`, `clip number ${i}`))
  const sizes: number[] = []
  server.use(
    http.post('/api/archive/delete', async ({ request }) => {
      const { ids } = (await request.json()) as { ids: string[] }
      sizes.push(ids.length)
      return HttpResponse.json({ deleted: ids.length, kept: 0 })
    }),
  )
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('clip number 0')

  await user.click(screen.getByRole('button', { name: /^Select: clip number 0/ }))
  await user.click(screen.getByRole('button', { name: 'Select all 201' }))
  await user.click(screen.getByRole('button', { name: 'Delete 201' }))
  const dialog = await screen.findByRole('alertdialog', { name: 'Delete 201 clips?' })
  await user.click(within(dialog).getByRole('button', { name: 'Delete 201' }))

  // Every id, in requests the server will accept - not 200 of them and silence.
  await waitFor(() => expect(sizes).toEqual([200, 1]))
  // Two hundred cards is a slow thing to draw in jsdom, and the point of the
  // test is the two hundred and first id.
}, 30_000)


// ---- sweeping a band across the grid ----

/** jsdom has no layout, so the cards are told where they are. */
function place(id: string, left: number, top: number, right: number, bottom: number) {
  const el = document.querySelector(`[data-clip-id="${id}"]`) as HTMLElement
  el.getBoundingClientRect = () =>
    ({ left, top, right, bottom, width: right - left, height: bottom - top, x: left, y: top, toJSON: () => ({}) }) as DOMRect
  return el
}

const band = () => document.querySelector('[data-band]') as HTMLElement | null

test('a sweep across the grid picks every clip it touches', async () => {
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')
  const grid = place('a', 0, 0, 100, 100).parentElement as HTMLElement
  place('b', 120, 0, 220, 100)

  fireEvent.mouseDown(grid, { button: 0, clientX: 5, clientY: 5 })
  fireEvent.mouseMove(window, { clientX: 200, clientY: 90 })
  expect(band()).not.toBeNull()
  expect(await screen.findByText('2 selected')).toBeInTheDocument()

  fireEvent.mouseUp(window)
  expect(band()).toBeNull()
  // What the sweep picked stays picked once the button is released.
  expect(screen.getByText('2 selected')).toBeInTheDocument()
})

test('the band keeps following the pointer after the first clip is picked', async () => {
  // The regression: picking changes the selection, and the band's effect used to
  // depend on it, so the first move tore down the listeners driving the drag.
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')
  const grid = place('a', 0, 0, 100, 100).parentElement as HTMLElement
  place('b', 120, 0, 220, 100)

  fireEvent.mouseDown(grid, { button: 0, clientX: 10, clientY: 10 })
  fireEvent.mouseMove(window, { clientX: 100, clientY: 80 })
  expect(band()!.style.width).toBe('90px')

  fireEvent.mouseMove(window, { clientX: 200, clientY: 150 })
  expect(band()!.style.width).toBe('190px')
  expect(band()!.style.height).toBe('140px')

  fireEvent.mouseUp(window)
  expect(band()).toBeNull()
})

test('a poster cannot be dragged away, so a sweep may start on one', async () => {
  // A browser answers a press-and-drag on an image by dragging the image, and
  // mousemove stops arriving - which is most of the grid, and most sweeps.
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')
  for (const img of document.querySelectorAll('img')) {
    expect(img).toHaveAttribute('draggable', 'false')
  }
})


test('a sweep that starts in the empty space beside the cards still picks them', async () => {
  // Where a marquee actually starts: the page margin, which is outside the grid
  // element - the cards only fill the middle of the page.
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')
  place('a', 200, 200, 300, 300)
  place('b', 320, 200, 420, 300)

  fireEvent.mouseDown(document.body, { button: 0, clientX: 5, clientY: 5 })
  fireEvent.mouseMove(window, { clientX: 500, clientY: 400 })
  expect(band()).not.toBeNull()
  expect(await screen.findByText('2 selected')).toBeInTheDocument()
  fireEvent.mouseUp(window)
})

test('a press on the search box is not a sweep', async () => {
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')
  const search = screen.getByLabelText('Search your prompts')

  fireEvent.mouseDown(search, { button: 0, clientX: 5, clientY: 5 })
  fireEvent.mouseMove(window, { clientX: 400, clientY: 400 })
  expect(band()).toBeNull()
  fireEvent.mouseUp(window)
})

test('a click on the empty space drops the selection', async () => {
  const user = userEvent.setup()
  renderWithProviders(<ArchivePage />, { route: '/archive' })
  await screen.findByText('a red car at dusk')
  await user.click(screen.getByRole('button', { name: /^Select: a red car/ }))
  expect(screen.getByText('1 selected')).toBeInTheDocument()

  fireEvent.mouseDown(document.body, { button: 0, clientX: 5, clientY: 5 })
  fireEvent.mouseUp(window)
  await waitFor(() => expect(screen.queryByText(/selected/)).not.toBeInTheDocument())
})

