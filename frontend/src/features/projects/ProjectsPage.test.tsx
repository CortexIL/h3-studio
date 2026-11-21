import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { expect, test } from 'vitest'
import { Route, Routes } from 'react-router'

import { keys } from '@/api/keys'
import type { ProjectSummary } from '@/api/types'
import { makeProjectSummary } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ProjectsPage } from './ProjectsPage'

function serveProjects(projects: ProjectSummary[]) {
  server.use(http.get('/api/projects', () => HttpResponse.json({ projects })))
}

test('lists my projects and marks the ones shared with me', async () => {
  serveProjects([
    makeProjectSummary({ id: 'p1', name: 'School film' }),
    makeProjectSummary({ id: 'p2', name: 'Ad', my_role: 'editor' }),
  ])
  renderWithProviders(<ProjectsPage />)
  expect(await screen.findByRole('link', { name: /School film/ })).toHaveAttribute('href', '/p/p1')
  expect(within(screen.getByRole('link', { name: /Ad/ })).getByText('Shared with you')).toBeInTheDocument()
  expect(within(screen.getByRole('link', { name: /School film/ })).queryByText('Shared with you')).toBeNull()
})

test('with no projects it explains what one is and offers to make one', async () => {
  serveProjects([])
  renderWithProviders(<ProjectsPage />)
  expect(await screen.findByText('No projects yet')).toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: 'New project' }).length).toBeGreaterThan(0)
})

test('making a project opens it', async () => {
  serveProjects([])
  let sent: unknown
  server.use(
    http.post('/api/projects', async ({ request }) => {
      sent = await request.json()
      return HttpResponse.json(makeProjectSummary({ id: 'new1', name: 'School film' }))
    }),
  )
  renderWithProviders(
    <Routes>
      <Route path="/" element={<ProjectsPage />} />
      <Route path="/p/:projectId" element={<p>opened</p>} />
    </Routes>,
  )
  await userEvent.click((await screen.findAllByRole('button', { name: 'New project' }))[0]!)
  const create = screen.getByRole('button', { name: 'Create project' })
  expect(create).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Project name'), '  School film  ')
  await userEvent.click(create)
  expect(await screen.findByText('opened')).toBeInTheDocument()
  expect(sent).toEqual({ name: 'School film' })
})

test('a failed load says so instead of claiming there are no projects', async () => {
  server.use(http.get('/api/projects', () => new HttpResponse(null, { status: 500 })))
  renderWithProviders(<ProjectsPage />)
  expect(await screen.findByText("Your projects didn't load")).toBeInTheDocument()
  expect(screen.queryByText('No projects yet')).toBeNull()
  serveProjects([makeProjectSummary({ id: 'p1', name: 'School film' })])
  await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByRole('link', { name: /School film/ })).toBeInTheDocument()
})

test('cancelling the new-project dialog forgets the typed name', async () => {
  serveProjects([])
  renderWithProviders(<ProjectsPage />)
  await userEvent.click((await screen.findAllByRole('button', { name: 'New project' }))[0]!)
  await userEvent.type(screen.getByLabelText('Project name'), 'Draft')
  await userEvent.keyboard('{Escape}')
  await userEvent.click(screen.getAllByRole('button', { name: 'New project' })[0]!)
  expect(screen.getByLabelText('Project name')).toHaveValue('')
})

test('each card counts its videos and people in plain words', async () => {
  serveProjects([
    makeProjectSummary({ id: 'p1', name: 'Solo', clips: 1, members: 1 }),
    makeProjectSummary({ id: 'p2', name: 'Team', clips: 3, members: 2 }),
  ])
  renderWithProviders(<ProjectsPage />)
  expect(within(await screen.findByRole('link', { name: /Solo/ })).getByText('1 video · 1 person')).toBeInTheDocument()
  expect(within(screen.getByRole('link', { name: /Team/ })).getByText('3 videos · 2 people')).toBeInTheDocument()
})

test('a failed refresh keeps the list already on screen', async () => {
  serveProjects([makeProjectSummary({ id: 'p1', name: 'School film' })])
  const { client } = renderWithProviders(<ProjectsPage />)
  await screen.findByRole('link', { name: /School film/ })
  server.use(http.get('/api/projects', () => new HttpResponse(null, { status: 500 })))
  await act(() => client.refetchQueries({ queryKey: keys.projects }))
  expect(client.getQueryState(keys.projects)?.status).toBe('error')
  // React Query tells the page on a timer; give it that turn before looking.
  await act(() => new Promise((r) => setTimeout(r, 20)))
  expect(screen.getByRole('link', { name: /School film/ })).toBeInTheDocument()
  expect(screen.queryByText("Your projects didn't load")).toBeNull()
})
