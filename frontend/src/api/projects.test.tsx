import { waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { useEffect } from 'react'
import { expect, test } from 'vitest'

import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { withProject } from './client'
import { useAddJobs } from './mutations'
import { useJobs } from './queries'

test('inside a project the feed asks for that project', async () => {
  let asked = ''
  server.use(http.get('/api/jobs', ({ request }) => { asked = request.url; return HttpResponse.json({ jobs: [] }) }))
  function Probe() { useJobs(); return null }
  renderWithProviders(<Probe />, { route: '/p/p1', path: '/p/:projectId' })
  await waitFor(() => expect(new URL(asked).searchParams.get('project')).toBe('p1'))
})

test('outside a project the feed asks without one', async () => {
  let asked = ''
  server.use(http.get('/api/jobs', ({ request }) => { asked = request.url; return HttpResponse.json({ jobs: [] }) }))
  function Probe() { useJobs(); return null }
  renderWithProviders(<Probe />)
  await waitFor(() => expect(asked).not.toBe(''))
  expect(new URL(asked).searchParams.has('project')).toBe(false)
})

test('a new clip is sent into the open project', async () => {
  let asked = ''
  server.use(http.post('/api/jobs', ({ request }) => { asked = request.url; return HttpResponse.json({ created: ['j'], count: 1 }) }))
  function Probe() { const { mutate } = useAddJobs(); useEffect(() => mutate({ prompts: 'a dog', split: 'single' }), [mutate]); return null }
  renderWithProviders(<Probe />, { route: '/p/p1', path: '/p/:projectId' })
  await waitFor(() => expect(new URL(asked).searchParams.get('project')).toBe('p1'))
})

test('withProject adds the project with the right separator', () => {
  expect(withProject('/api/jobs?limit=300', 'p 1')).toBe('/api/jobs?limit=300&project=p%201')
  expect(withProject('/api/archive', undefined)).toBe('/api/archive')
})
