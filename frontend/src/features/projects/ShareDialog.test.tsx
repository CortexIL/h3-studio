import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { Route, Routes } from 'react-router'
import { expect, test } from 'vitest'

import { makeProject } from '@/test/factories'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { ShareDialog } from './ShareDialog'

const ownerProject = makeProject({ id: 'p1', name: 'School film' })
const dana = { user_id: 'u-dana', email: 'dana@school.test', role: 'editor' as const, avatar_url: null, is_me: false }
const withMember = makeProject({ ...ownerProject, members: [...ownerProject.members, dana] })
const asEditor = makeProject({
  id: 'p1',
  my_role: 'editor',
  members: [
    { user_id: 'u-boss', email: 'boss@school.test', role: 'owner', avatar_url: null, is_me: false },
    { ...dana, user_id: 'u-me', email: 'me@h3.local', is_me: true },
  ],
})

test('the owner adds a teammate by email with a role', async () => {
  let sent: unknown
  server.use(http.post('/api/projects/p1/members', async ({ request }) => { sent = await request.json(); return HttpResponse.json(withMember) }))
  renderWithProviders(<ShareDialog project={ownerProject} open onOpenChange={() => {}} />)
  await userEvent.type(screen.getByLabelText('Their email'), 'dana@school.test')
  await userEvent.click(screen.getByRole('button', { name: 'Add' }))
  await waitFor(() => expect(sent).toEqual({ email: 'dana@school.test', role: 'editor' }))
  await waitFor(() => expect(screen.getByLabelText('Their email')).toHaveValue(''))
})

test('an unknown email says what to do', async () => {
  server.use(http.post('/api/projects/p1/members', () => HttpResponse.json({ detail: 'server says no' }, { status: 404 })))
  renderWithProviders(<ShareDialog project={ownerProject} open onOpenChange={() => {}} />)
  await userEvent.type(screen.getByLabelText('Their email'), 'nobody@x.test')
  await userEvent.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByText(/Nobody has an H3 account with that email/)).toBeInTheDocument()
  expect(screen.queryByText(/server says no/)).not.toBeInTheDocument()
})

test('someone already in the project says so', async () => {
  server.use(http.post('/api/projects/p1/members', () => HttpResponse.json({ detail: 'server says no' }, { status: 409 })))
  renderWithProviders(<ShareDialog project={withMember} open onOpenChange={() => {}} />)
  await userEvent.type(screen.getByLabelText('Their email'), 'dana@school.test')
  await userEvent.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByText("They're already in this project.")).toBeInTheDocument()
  expect(screen.queryByText(/server says no/)).not.toBeInTheDocument()
})

test('the new person\'s role select has a name', () => {
  renderWithProviders(<ShareDialog project={ownerProject} open onOpenChange={() => {}} />)
  expect(screen.getByRole('combobox', { name: 'What they can do' })).toBeInTheDocument()
})

test('Remove says whose row it is', () => {
  renderWithProviders(<ShareDialog project={withMember} open onOpenChange={() => {}} />)
  expect(screen.getByRole('button', { name: 'Remove' })).toHaveAccessibleDescription('dana@school.test')
})

test('changing a role sends it to that person', async () => {
  let sent: unknown
  let url = ''
  server.use(http.patch('/api/projects/p1/members/:uid', async ({ request, params }) => { url = String(params.uid); sent = await request.json(); return HttpResponse.json(withMember) }))
  renderWithProviders(<ShareDialog project={withMember} open onOpenChange={() => {}} />)
  // jsdom lacks the DOM bits Radix Select leans on.
  Element.prototype.scrollIntoView = () => {}
  Element.prototype.hasPointerCapture = () => false
  screen.getByRole('combobox', { name: 'dana@school.test' }).focus()
  await userEvent.keyboard('{Enter}')
  fireEvent.click(await screen.findByRole('option', { name: 'Can watch' }))
  await waitFor(() => expect(sent).toEqual({ role: 'viewer' }))
  expect(url).toBe('u-dana')
})

test('Add stays off until there is an email', () => {
  renderWithProviders(<ShareDialog project={ownerProject} open onOpenChange={() => {}} />)
  expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
})

test('the owner removes someone', async () => {
  let removed = ''
  server.use(http.delete('/api/projects/p1/members/:uid', ({ params }) => { removed = String(params.uid); return HttpResponse.json({ ok: true }) }))
  renderWithProviders(<ShareDialog project={withMember} open onOpenChange={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
  await waitFor(() => expect(removed).toBe('u-dana'))
})

test('a teammate sees who is in it and can only leave', async () => {
  renderWithProviders(<ShareDialog project={asEditor} open onOpenChange={() => {}} />)
  expect(screen.getByText('boss@school.test')).toBeInTheDocument()
  expect(screen.queryByLabelText('Their email')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Leave project' })).toBeInTheDocument()
})

test('leaving asks first, then removes you and goes home', async () => {
  let removed = ''
  server.use(http.delete('/api/projects/p1/members/:uid', ({ params }) => { removed = String(params.uid); return HttpResponse.json({ ok: true }) }))
  renderWithProviders(
    <Routes>
      <Route path="/" element={<p>home</p>} />
      <Route path="/p/:projectId" element={<ShareDialog project={asEditor} open onOpenChange={() => {}} />} />
    </Routes>,
    { route: '/p/p1' },
  )
  await userEvent.click(screen.getByRole('button', { name: 'Leave project' }))
  expect(await screen.findByText(/You won't see its videos any more/)).toBeInTheDocument()
  await userEvent.click(screen.getAllByRole('button', { name: 'Leave project' }).at(-1)!)
  await waitFor(() => expect(removed).toBe('u-me'))
  expect(await screen.findByText('home')).toBeInTheDocument()
})

test('typing part of an email suggests matching accounts', async () => {
  const asked: string[] = []
  server.use(
    http.get('/api/projects/p1/people', ({ request }) => {
      asked.push(new URL(request.url).searchParams.get('q') ?? '')
      return HttpResponse.json({ people: [{ email: 'dana@school.test' }, { email: 'dave@school.test' }] })
    }),
  )
  renderWithProviders(<ShareDialog project={ownerProject} open onOpenChange={() => {}} />)
  const box = screen.getByLabelText('Their email')
  await userEvent.type(box, 'da')
  const list = document.getElementById(box.getAttribute('list') ?? '')
  await waitFor(() =>
    expect([...(list?.querySelectorAll('option') ?? [])].map((o) => o.value)).toEqual(['dana@school.test', 'dave@school.test']),
  )
  // One letter asks nothing; the server is only asked once typing pauses.
  expect(asked).toEqual(['da'])
})

test('a teammate who cannot add people is never sent suggestions', async () => {
  let asked = false
  server.use(http.get('/api/projects/p1/people', () => { asked = true; return HttpResponse.json({ people: [] }) }))
  renderWithProviders(<ShareDialog project={asEditor} open onOpenChange={() => {}} />)
  expect(await screen.findByText('boss@school.test')).toBeInTheDocument()
  expect(asked).toBe(false)
})
