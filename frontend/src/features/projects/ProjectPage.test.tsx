import { screen } from '@testing-library/react'
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
  serveProject(makeProject({ id: 'p1' }))
  serveJobs([makeJob({ status: 'done', video_url: '/api/video/j1', made_by: 'dana@school.test', can_manage: false })])
  renderProject('/p/p1')
  expect(await screen.findByText('by dana')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
})

test('a project you are not in says so', async () => {
  server.use(http.get('/api/projects/p1', () => HttpResponse.json({ detail: 'no such project' }, { status: 404 })))
  renderProject('/p/p1')
  expect(await screen.findByText("This project isn't here")).toBeInTheDocument()
})
