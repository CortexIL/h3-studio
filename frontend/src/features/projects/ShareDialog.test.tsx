import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
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
  server.use(http.post('/api/projects/p1/members', () => HttpResponse.json({ detail: 'x' }, { status: 404 })))
  renderWithProviders(<ShareDialog project={ownerProject} open onOpenChange={() => {}} />)
  await userEvent.type(screen.getByLabelText('Their email'), 'nobody@x.test')
  await userEvent.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByText(/Nobody has an H3 account with that email/)).toBeInTheDocument()
})

test('someone already in the project says so', async () => {
  server.use(http.post('/api/projects/p1/members', () => HttpResponse.json({ detail: 'x' }, { status: 409 })))
  renderWithProviders(<ShareDialog project={withMember} open onOpenChange={() => {}} />)
  await userEvent.type(screen.getByLabelText('Their email'), 'dana@school.test')
  await userEvent.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByText("They're already in this project.")).toBeInTheDocument()
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
  renderWithProviders(<ShareDialog project={asEditor} open onOpenChange={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: 'Leave project' }))
  expect(await screen.findByText(/You won't see its videos any more/)).toBeInTheDocument()
  await userEvent.click(screen.getAllByRole('button', { name: 'Leave project' }).at(-1)!)
  await waitFor(() => expect(removed).toBe('u-me'))
})
