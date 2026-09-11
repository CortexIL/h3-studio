import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'

import { makeJob, makeStatus } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ClipViewerDialog } from './ClipViewerDialog'
import { useViewerList } from './useClipViewer'

const jobs = {
  a: makeJob({ id: 'a', status: 'done', prompt: 'first clip', video_url: '/api/video/a', seed: 42 }),
  b: makeJob({ id: 'b', status: 'done', prompt: 'second clip', video_url: '/api/video/b' }),
}

beforeEach(() => {
  useViewerList.setState({ ids: ['a', 'b'] })
  server.use(
    http.get('/api/status', () => HttpResponse.json(makeStatus())),
    http.get('/api/jobs/:id', ({ params }) => {
      const job = jobs[params.id as keyof typeof jobs]
      return job ? HttpResponse.json(job) : HttpResponse.json({ detail: 'no such job' }, { status: 404 })
    }),
  )
})

test('opens from the URL and steps through the list with the arrow keys', async () => {
  const user = userEvent.setup()
  renderWithProviders(<ClipViewerDialog />, { route: '/archive?clip=a' })
  const dialog = await screen.findByRole('dialog', { name: 'Clip 1 of 2' })
  expect(await screen.findByText('first clip')).toBeInTheDocument()
  expect(dialog).toHaveTextContent('42') // the seed
  expect(screen.getByRole('button', { name: 'Previous clip' })).toBeDisabled()

  await user.keyboard('{ArrowRight}')
  expect(await screen.findByText('second clip')).toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: 'Clip 2 of 2' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Next clip' })).toBeDisabled()
})

test('a clip that no longer exists says so', async () => {
  renderWithProviders(<ClipViewerDialog />, { route: '/?clip=gone' })
  expect(await screen.findByText("This clip isn't available")).toBeInTheDocument()
})

test('nothing renders without ?clip', () => {
  renderWithProviders(<ClipViewerDialog />, { route: '/archive' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
