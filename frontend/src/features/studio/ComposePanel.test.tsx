import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'

import type { NewJobsBody } from '@/api/types'
import { makeStatus } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ComposePanel } from './ComposePanel'
import { useCompose } from './composeStore'

beforeEach(() => {
  sessionStorage.clear()
  useCompose.setState({ prompt: '', split: 'single', seconds: 10, preset: 'final', mode: 'i2v', takes: 1, refs: [], startFrame: null, endFrame: null, initialized: false })
  server.use(
    http.get('/api/status', () => HttpResponse.json(makeStatus())),
    http.post('/api/estimate', () =>
      HttpResponse.json({ clips: 1, gpu: 'x', rate_per_hour: 1, minutes_per_clip: 5, render_minutes: 5, startup_minutes: 3, total_minutes: 8, cost_usd: 0.13, cost_per_clip_usd: 0.08, confidence: 'estimated' }),
    ),
  )
})

test('the add button is disabled until there is a prompt', async () => {
  renderWithProviders(<ComposePanel />)
  expect(screen.getByRole('button', { name: 'Add to the queue' })).toBeDisabled()
  await userEvent.setup().type(screen.getByLabelText('Prompt'), 'a red car')
  expect(screen.getByRole('button', { name: 'Add to the queue' })).toBeEnabled()
})

test('line mode counts clips and sends split=lines with the takes', async () => {
  let sent: NewJobsBody | null = null
  server.use(
    http.post('/api/jobs', async ({ request }) => {
      sent = (await request.json()) as NewJobsBody
      return HttpResponse.json({ created: [], count: 6 })
    }),
  )
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.click(screen.getByRole('radio', { name: 'Line = clip' }))
  await user.type(screen.getByLabelText('Prompt'), 'one{Enter}two{Enter}three')
  await user.click(screen.getByRole('button', { name: 'Increase takes' }))
  expect(screen.getByText(/3 lines → 6 clips/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Add 6 clips to the queue' }))
  await waitFor(() => expect(sent).not.toBeNull())
  expect(sent!.split).toBe('lines')
  expect(sent!.count).toBe(2)
})

test('Ctrl+Enter adds to the queue and the prompt clears afterwards', async () => {
  let calls = 0
  server.use(
    http.post('/api/jobs', () => {
      calls++
      return HttpResponse.json({ created: ['x'], count: 1 })
    }),
  )
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  const box = screen.getByLabelText('Prompt')
  await user.type(box, 'a lighthouse in a storm')
  await user.keyboard('{Control>}{Enter}{/Control}')
  await waitFor(() => expect(calls).toBe(1))
  await waitFor(() => expect(box).toHaveValue(''))
})

test('Use again restores a multi-line prompt as one prompt, never split', () => {
  useCompose.setState({ split: 'lines' })
  useCompose.getState().loadFromJob({ prompt: 'first line\nsecond line', seconds: 6, preset: 'draft', mode: 't2v', ref_images: [] })
  const s = useCompose.getState()
  expect(s.split).toBe('single')
  expect(s.prompt).toBe('first line\nsecond line')
  expect(s.takes).toBe(1)
})

test('server defaults seed the form once, then the user wins', async () => {
  renderWithProviders(<ComposePanel />)
  await waitFor(() => expect(useCompose.getState().initialized).toBe(true))
  useCompose.getState().setSeconds(6)
  useCompose.getState().applyDefaults(makeStatus().config)
  expect(useCompose.getState().seconds).toBe(6)
})

// ---- start to end ----

const ready = (id: string, key: string) =>
  ({ id, name: key, status: 'ready' as const, progress: 1, key })

function captureJobs() {
  const sent: { body: NewJobsBody | null } = { body: null }
  server.use(
    http.post('/api/jobs', async ({ request }) => {
      sent.body = (await request.json()) as NewJobsBody
      return HttpResponse.json({ created: ['x'], count: 1 })
    }),
  )
  return sent
}

test('start to end sends both frames, the start one first', async () => {
  const sent = captureJobs()
  // initialized, or the status poll would seed the mode back to the default
  useCompose.setState({
    initialized: true,
    mode: 'flf2v',
    startFrame: ready('a', 'uploads/u1/start.png'),
    endFrame: ready('b', 'uploads/u1/end.png'),
  })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'a door swinging open')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.mode).toBe('flf2v')
  expect(sent.body!.ref_images).toEqual(['uploads/u1/start.png', 'uploads/u1/end.png'])
})

test('the button names the frame that is missing', async () => {
  useCompose.setState({ initialized: true, mode: 'flf2v' })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'a door swinging open')
  expect(screen.getByRole('button', { name: 'Add a start frame' })).toBeDisabled()

  useCompose.setState({ startFrame: ready('a', 'uploads/u1/start.png') })
  expect(await screen.findByRole('button', { name: 'Add an end frame' })).toBeDisabled()
})

test('text to video never sends the references it is holding', async () => {
  const sent = captureJobs()
  useCompose.setState({
    initialized: true,
    mode: 't2v',
    refs: [ready('a', 'uploads/u1/one.png')],
  })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'a lighthouse')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.ref_images).toEqual([])
})

test('switching modes never discards what was already picked', () => {
  useCompose.setState({
    initialized: true,
    mode: 'i2v',
    refs: [ready('a', 'uploads/u1/one.png'), ready('b', 'uploads/u1/two.png')],
  })
  useCompose.getState().setMode('flf2v')
  useCompose.getState().setMode('i2v')
  expect(useCompose.getState().refs).toHaveLength(2)
})

test('Use again on a start to end job restores both frames in order', () => {
  useCompose.getState().loadFromJob({
    prompt: 'p', seconds: 6, preset: 'draft', mode: 'flf2v',
    ref_images: ['uploads/u1/start.png', 'uploads/u1/end.png'],
  })
  const s = useCompose.getState()
  expect(s.startFrame?.key).toBe('uploads/u1/start.png')
  expect(s.endFrame?.key).toBe('uploads/u1/end.png')
  expect(s.refs).toEqual([])
})
