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
  useCompose.setState({ prompt: '', split: 'single', shots: [], seconds: 10, preset: 'final', mode: 'i2v', takes: 1, keepAudio: true, sound: '', music: '', steps: null, shiftVideo: null, shiftAudio: null, width: null, height: null, seed: null, refs: [], startFrame: null, endFrame: null, extendSource: null, initialized: false })
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

// ---- sound ----

test('turning sound off sends that with the clip', async () => {
  const sent = captureJobs()
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'a door swinging open')
  await user.click(screen.getByRole('switch', { name: 'Sound' }))
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.keep_audio).toBe(false)
})

test('the server decides where the sound switch starts', async () => {
  const status = makeStatus()
  server.use(
    http.get('/api/status', () =>
      HttpResponse.json({ ...status, config: { ...status.config, keep_audio: false } })),
  )
  renderWithProviders(<ComposePanel />)
  await waitFor(() => expect(useCompose.getState().keepAudio).toBe(false))
})

test('Use again brings back whether that clip had sound', () => {
  useCompose.getState().loadFromJob({
    prompt: 'p', seconds: 10, preset: 'final', mode: 'i2v', ref_images: [], keep_audio: false,
  })
  expect(useCompose.getState().keepAudio).toBe(false)
})

test('a clip that never chose leaves the switch where it is', () => {
  useCompose.setState({ keepAudio: false })
  useCompose.getState().loadFromJob({
    prompt: 'p', seconds: 10, preset: 'final', mode: 'i2v', ref_images: [], keep_audio: null,
  })
  expect(useCompose.getState().keepAudio).toBe(false)
})

// ---- extend ----

test('extend sends the source clip as its only reference', async () => {
  const sent = captureJobs()
  useCompose.setState({
    initialized: true,
    mode: 'extend',
    extendSource: {
      from: 'clip',
      label: 'the clip being continued',
      tile: ready('c', 'uploads/u1/tail.mp4'),
    },
  })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'she turns and walks away')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.mode).toBe('extend')
  expect(sent.body!.ref_images).toEqual(['uploads/u1/tail.mp4'])
})

test('extend cannot be queued without a video to continue', async () => {
  useCompose.setState({ initialized: true, mode: 'extend' })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'she turns and walks away')
  expect(screen.getByRole('button', { name: 'Choose a video to continue' })).toBeDisabled()
})

test('Use again on an extension restores the clip it continues', () => {
  useCompose.getState().loadFromJob({
    prompt: 'p', seconds: 10, preset: 'final', mode: 'extend',
    ref_images: ['uploads/u1/tail.mp4'],
  })
  const s = useCompose.getState()
  expect(s.extendSource?.tile.key).toBe('uploads/u1/tail.mp4')
  expect(s.refs).toEqual([])
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

test('an extension can be given a destination, and sends it after the source', async () => {
  const sent = captureJobs()
  useCompose.setState({
    initialized: true,
    mode: 'extend',
    extendSource: { from: 'clip', label: 'the clip', tile: ready('c', 'uploads/u1/tail.mp4') },
    endFrame: ready('e', 'uploads/u1/arrive.png'),
  })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'it keeps going and lands on the stone')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  // source first, destination second - the order the server binds them in
  expect(sent.body!.ref_images).toEqual(['uploads/u1/tail.mp4', 'uploads/u1/arrive.png'])
})

test('an extension without a destination still sends just the source', async () => {
  const sent = captureJobs()
  useCompose.setState({
    initialized: true,
    mode: 'extend',
    extendSource: { from: 'clip', label: 'the clip', tile: ready('c', 'uploads/u1/tail.mp4') },
  })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'it keeps going')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.ref_images).toEqual(['uploads/u1/tail.mp4'])
})

// ---- sound direction ----

test('sound direction travels as its own fields, trimmed', async () => {
  const sent = captureJobs()
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'a lantern on a wall')
  await user.type(screen.getByLabelText('Soundscape'), '  wind in the leaves ')
  await user.type(screen.getByLabelText('Music'), 'slow piano')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.sound).toBe('wind in the leaves')
  expect(sent.body!.music).toBe('slow piano')
})

test('with sound off the fields are hidden and nothing is sent', async () => {
  const sent = captureJobs()
  useCompose.setState({ sound: 'wind', music: 'piano' })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.click(screen.getByRole('switch', { name: 'Sound' }))
  expect(screen.queryByLabelText('Soundscape')).toBeNull()
  await user.type(screen.getByLabelText('Prompt'), 'a lantern on a wall')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.sound).toBeUndefined()
  expect(sent.body!.music).toBeUndefined()
})

test('Use again brings the direction back into its own fields', () => {
  useCompose.getState().loadFromJob({
    prompt: 'p', seconds: 6, preset: 'final', mode: 'i2v', ref_images: [],
    keep_audio: true, sound: 'wind', music: 'piano',
  })
  const s = useCompose.getState()
  expect(s.sound).toBe('wind')
  expect(s.music).toBe('piano')
  expect(s.prompt).toBe('p')
})

// ---- advanced controls ----

test('controls travel only when set, and the seed only for a single take', async () => {
  const sent = captureJobs()
  useCompose.setState({ steps: 24, shiftVideo: 9.5, width: 1920, height: 1088, seed: 7, takes: 1 })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'a lantern')
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.steps).toBe(24)
  expect(sent.body!.shift_video).toBe(9.5)
  expect(sent.body!.width).toBe(1920)
  expect(sent.body!.height).toBe(1088)
  expect(sent.body!.seed).toBe(7)
  expect(sent.body!.shift_audio).toBeUndefined()
})

test('a bad size blocks the button and says why', async () => {
  useCompose.setState({ width: 1000, height: 576 })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.type(screen.getByLabelText('Prompt'), 'a lantern')
  expect(screen.getByRole('button', { name: 'Fix the advanced controls' })).toBeDisabled()
  expect(screen.getAllByText('Size: multiples of 32').length).toBeGreaterThan(0)
})

test('Use again restores the controls but never the seed', () => {
  useCompose.getState().loadFromJob({
    prompt: 'p', seconds: 6, preset: 'final', mode: 'i2v', ref_images: [],
    steps: 24, shift_video: 9.5, shift_audio: null, width: 1920, height: 1088,
  })
  const s = useCompose.getState()
  expect([s.steps, s.shiftVideo, s.shiftAudio, s.width, s.height, s.seed]).toEqual([24, 9.5, null, 1920, 1088, null])
})


// ---- shots ----

test('shots become one SHOT-numbered prompt that the server never splits', async () => {
  const sent = captureJobs()
  useCompose.setState({
    split: 'shots',
    shots: [
      { id: 'a', text: 'A soldier walks to the gate.', transition: 'cut' },
      { id: 'b', text: 'The sign fills the frame.', transition: 'match' },
      { id: 'c', text: '', transition: 'cut' },
    ],
  })
  const user = userEvent.setup()
  renderWithProviders(<ComposePanel />)
  await user.click(screen.getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent.body).not.toBeNull())
  expect(sent.body!.split).toBe('single')
  expect(sent.body!.prompts).toBe('SHOT 1: A soldier walks to the gate.\nSHOT 2: Match cut to the sign fills the frame.')
})

test('shots mode with nothing written cannot be queued', () => {
  useCompose.setState({ split: 'shots', shots: [{ id: 'a', text: '  ', transition: 'cut' }] })
  renderWithProviders(<ComposePanel />)
  expect(screen.getByRole('button', { name: 'Add to the queue' })).toBeDisabled()
})

test('switching to shots seeds two empty cards and back keeps them', () => {
  useCompose.getState().setSplit('shots')
  expect(useCompose.getState().shots).toHaveLength(2)
  useCompose.getState().setSplit('single')
  expect(useCompose.getState().shots).toHaveLength(2)
})
