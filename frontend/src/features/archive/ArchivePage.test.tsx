import { screen, waitFor, within } from '@testing-library/react'
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
    keep_audio: true,
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
