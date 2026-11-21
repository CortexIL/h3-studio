import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { expect, test } from 'vitest'
import { Route, Routes } from 'react-router'

import type { Job, Project } from '@/api/types'
import { ArchivePage } from '@/features/archive/ArchivePage'
import { StudioPage } from '@/features/studio/StudioPage'
import { makeJob, makeProject } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ProjectPage } from './ProjectPage'

function serveProject(p: Project) {
  server.use(http.get(`/api/projects/${p.id}`, () => HttpResponse.json(p)))
}

function serveJobs(jobs: Job[]) {
  server.use(http.get('/api/jobs', () => HttpResponse.json({ jobs })))
}

function renderProject(route: string) {
  return renderWithProviders(<ProjectRoutes />, { route, path: '/p/:projectId/*' })
}

// The same subtree the router mounts, under a splat so the test can hold it in one <Route>.
function ProjectRoutes() {
  return (
    <Routes>
      <Route element={<ProjectPage />}>
        <Route index element={<StudioPage />} />
        <Route path="clips" element={<ArchivePage />} />
      </Route>
    </Routes>
  )
}

test('shows the project name and its two tabs', async () => {
  serveProject(makeProject({ id: 'p1', name: 'School film' }))
  renderProject('/p/p1')
  expect(await screen.findByRole('heading', { name: 'School film' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Studio' })).toHaveAttribute('href', '/p/p1')
  expect(screen.getByRole('link', { name: 'Clips' })).toHaveAttribute('href', '/p/p1/clips')
})

test('a viewer gets the watch-only note instead of the make panel', async () => {
  serveProject(makeProject({ id: 'p1', my_role: 'viewer' }))
  renderProject('/p/p1')
  expect(await screen.findByText('You can watch this project')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /add .* queue/i })).not.toBeInTheDocument()
})

test("a teammate's clip says who made it and offers no delete", async () => {
  const user = userEvent.setup()
  serveProject(makeProject({ id: 'p1' }))
  serveJobs([makeJob({ status: 'done', video_url: '/api/video/j1', made_by: 'dana@school.test', can_manage: false })])
  renderProject('/p/p1')
  expect(await screen.findByText('by dana')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'More actions' }))
  await screen.findByRole('menuitem', { name: /Open/ })
  expect(screen.queryByRole('menuitem', { name: /Delete/ })).not.toBeInTheDocument()
})

test('a clip you can manage does offer delete', async () => {
  const user = userEvent.setup()
  serveProject(makeProject({ id: 'p1' }))
  serveJobs([makeJob({ status: 'done', video_url: '/api/video/j1', can_manage: true })])
  renderProject('/p/p1')
  await user.click(await screen.findByRole('button', { name: 'More actions' }))
  expect(await screen.findByRole('menuitem', { name: /Delete/ })).toBeInTheDocument()
})

test('a viewer sees no clear buttons in the feed', async () => {
  serveProject(makeProject({ id: 'p1', my_role: 'viewer' }))
  serveJobs([makeJob({ status: 'done', video_url: '/api/video/j1', can_make: false, can_manage: false })])
  renderProject('/p/p1')
  await screen.findByText('You can watch this project')
  await screen.findByRole('button', { name: 'More actions' })
  expect(screen.queryByRole('button', { name: /clear/i })).not.toBeInTheDocument()
})

test('an editor still has the clear buttons', async () => {
  serveProject(makeProject({ id: 'p1', my_role: 'editor' }))
  serveJobs([makeJob({ status: 'done', video_url: '/api/video/j1' })])
  renderProject('/p/p1')
  expect(await screen.findByRole('button', { name: /clear finished/i })).toBeInTheDocument()
})

test('a viewer with a clip selected gets no run-again or delete for the selection', async () => {
  const user = userEvent.setup()
  serveProject(makeProject({ id: 'p1', my_role: 'viewer' }))
  server.use(
    http.get('/api/archive', () =>
      HttpResponse.json({ clips: [makeJob({ status: 'done', prompt: 'a red car', video_url: '/api/video/c1' })], next_cursor: null, total: 1 }),
    ),
  )
  renderProject('/p/p1/clips')
  await user.click(await screen.findByRole('button', { name: /^Select: a red car/ }))
  expect(await screen.findByText('1 selected')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Download 1' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /run again/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^Delete/ })).not.toBeInTheDocument()
})

test('a project you are not in says so', async () => {
  server.use(http.get('/api/projects/p1', () => HttpResponse.json({ detail: 'no such project' }, { status: 404 })))
  renderProject('/p/p1')
  expect(await screen.findByText("This project isn't here")).toBeInTheDocument()
})
