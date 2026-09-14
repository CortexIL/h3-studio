import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'

import type { NewJobsBody } from '@/api/types'
import { useLayout } from '@/lib/layout'
import { presetLabel, modeLabel } from '@/lib/format'
import { makeStatus } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ClassicComposePanel } from './ClassicComposePanel'
import { CLASSIC_PRESET_ORDER, classicPayload } from './classic'
import { draftOf, useCompose } from './composeStore'
import { StudioPage } from './StudioPage'

beforeEach(() => {
  useLayout.setState({ layout: 'studio' })
  useCompose.setState({ prompt: '', split: 'single', shots: [], seconds: 10, preset: 'turbo', mode: 'i2v', takes: 1, keepAudio: true, sound: '', music: '', steps: null, shiftVideo: null, shiftAudio: null, width: null, height: null, seed: null, refs: [], startFrame: null, endFrame: null, extendSource: null, keyframes: [], audio: null, refVideos: [], refAudios: [], effects: [], initialized: true })
  server.use(http.get('/api/status', () => HttpResponse.json(makeStatus())))
})

test('the classic panel speaks the old names', async () => {
  renderWithProviders(<ClassicComposePanel />)
  const panel = await screen.findByRole('region', { name: 'Create' })
  expect(panel).toHaveAttribute('data-layout', 'classic')
  expect(within(panel).getByText('Mode')).toBeInTheDocument()
  expect(within(panel).getByText('Prompt')).toBeInTheDocument()
  expect(within(panel).getByText('Seconds')).toBeInTheDocument()
  expect(within(panel).getByText('Takes')).toBeInTheDocument()
  expect(within(panel).getByText('Quality')).toBeInTheDocument()
  expect(within(panel).getByText('Sound')).toBeInTheDocument()
  expect(within(panel).getByRole('button', { name: 'Add to the queue' })).toBeDisabled()
  // Nothing the beta added is on it.
  expect(within(panel).queryByText(/Sounds|Music|Effects|Shape|Speed|Improve|Fine-tuning|keyframe/i)).not.toBeInTheDocument()
})

test('the request carries only what the panel shows, whatever the draft still holds', async () => {
  let sent: NewJobsBody | null = null
  server.use(
    http.post('/api/jobs', async ({ request }) => {
      sent = (await request.json()) as NewJobsBody
      return HttpResponse.json({ created: ['j1'], count: 1 })
    }),
  )
  // A draft the new panel left behind: effects, sound direction, fine-tuning, shots.
  useCompose.setState({ effects: ['bullet_time'], sound: 'wind', music: 'guitar', steps: 6, width: 768, height: 1344, seed: 42, split: 'shots' })
  renderWithProviders(<ClassicComposePanel />)
  const panel = await screen.findByRole('region', { name: 'Create' })
  await userEvent.type(within(panel).getByLabelText('Prompt'), 'a boat on a lake')
  await userEvent.click(within(panel).getByRole('button', { name: 'Add to the queue' }))
  await waitFor(() => expect(sent).not.toBeNull())
  expect(sent).toEqual({ prompts: 'a boat on a lake', split: 'single', seconds: 10, preset: 'turbo', mode: 'i2v', count: 1, keep_audio: true, ref_images: [] })
})

test('classicPayload never leaks the beta fields', () => {
  const s = draftOf({ ...useCompose.getState(), prompt: 'x', effects: ['dark_magic'], sound: 's', steps: 8, seed: 1 })
  const body = classicPayload(s)
  expect(Object.keys(body).sort()).toEqual(['count', 'keep_audio', 'mode', 'preset', 'prompts', 'ref_images', 'seconds', 'split'])
})

test('the old names come back everywhere while classic is on', () => {
  expect(presetLabel('turbo')).toBe('Quick')
  expect(modeLabel('i2v')).toBe('From an image')
  useLayout.setState({ layout: 'classic' })
  expect(presetLabel('turbo')).toBe('Turbo')
  expect(presetLabel('final')).toBe('Final')
  expect(modeLabel('i2v')).toBe('Reference')
  expect(modeLabel('flf2v')).toBe('Start to end')
})

test('the studio page swaps panels with the layout', async () => {
  server.use(http.get('/api/jobs', () => HttpResponse.json({ jobs: [] })))
  renderWithProviders(<StudioPage />)
  const create = await screen.findByRole('region', { name: 'Create' })
  expect(create).not.toHaveAttribute('data-layout', 'classic')
  useLayout.setState({ layout: 'classic' })
  await waitFor(() => expect(screen.getByRole('region', { name: 'Create' })).toHaveAttribute('data-layout', 'classic'))
})

test('classic offers the same four qualities under the old names and keeps the chosen one', async () => {
  useLayout.setState({ layout: 'classic' })
  useCompose.setState({ preset: 'sharp' })
  renderWithProviders(<ClassicComposePanel />)
  const panel = await screen.findByRole('region', { name: 'Create' })
  const trigger = within(panel).getByRole('combobox', { name: 'Quality' })
  // The beta's qualities are no longer swapped out from under the draft.
  expect(trigger).toHaveTextContent('Sharp')
  expect(useCompose.getState().preset).toBe('sharp')
  expect(CLASSIC_PRESET_ORDER).toEqual(['turbo', 'balanced', 'sharp', 'final'])
})
