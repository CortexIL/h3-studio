import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'

import type { AdminUser } from '@/api/types'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { UsersTab } from './UsersTab'

const usage = { queued: 0, running: 0, done: 3, failed: 0, cancelled: 0, stored_bytes: 1_000, last_job_at: null }
const users: AdminUser[] = [
  { id: 'u-admin', email: 'you@h3.local', role: 'admin', is_active: true, token_version: 1, avatar_url: null, created_at: '2026-09-11T08:00:00Z', usage },
  { id: 'u-2', email: 'dana@h3.local', role: 'user', is_active: true, token_version: 1, avatar_url: null, created_at: '2026-09-11T09:00:00Z', usage },
]
let patches: { id: string; body: unknown }[] = []

beforeEach(() => {
  patches = []
  server.use(
    http.get('/api/me', () => HttpResponse.json({ id: 'u-admin', email: 'you@h3.local', role: 'admin' })),
    http.get('/api/admin/users', () => HttpResponse.json({ users })),
    http.patch('/api/admin/users/:id', async ({ params, request }) => {
      const body = await request.json()
      patches.push({ id: String(params.id), body })
      return HttpResponse.json(users.find((u) => u.id === params.id))
    }),
  )
})

async function openMenu(email: string) {
  const user = userEvent.setup()
  renderWithProviders(<UsersTab />)
  await user.click(await screen.findByRole('button', { name: `Actions for ${email}` }))
  return user
}

test('a reset password is generated, saved, then shown once to copy', async () => {
  const user = await openMenu('dana@h3.local')
  await user.click(await screen.findByRole('menuitem', { name: /Reset password/ }))
  const dialog = await screen.findByRole('dialog', { name: 'Reset password' })
  await user.click(within(dialog).getByRole('button', { name: /Generate/ }))
  const password = (within(dialog).getByLabelText('Password') as HTMLInputElement).value
  expect(password).toHaveLength(16)
  await user.click(within(dialog).getByRole('button', { name: 'Set password' }))
  const done = await screen.findByRole('dialog', { name: 'Password changed' })
  expect(patches).toEqual([{ id: 'u-2', body: { password } }])
  expect(within(done).getByText(password)).toBeInTheDocument()
  expect(within(done).getByRole('button', { name: /Copy/ })).toBeInTheDocument()
})

test('a short password is caught before it is sent', async () => {
  const user = await openMenu('dana@h3.local')
  await user.click(await screen.findByRole('menuitem', { name: /Reset password/ }))
  const dialog = await screen.findByRole('dialog', { name: 'Reset password' })
  await user.type(within(dialog).getByLabelText('Password'), 'short')
  await user.click(within(dialog).getByRole('button', { name: 'Set password' }))
  expect(within(dialog).getByText('Use at least 8 characters.')).toBeInTheDocument()
  expect(patches).toEqual([])
})

test("your own row can't be demoted or disabled", async () => {
  await openMenu('you@h3.local')
  expect(await screen.findByText("You can't demote or disable yourself.")).toBeInTheDocument()
  expect(screen.queryByRole('menuitem', { name: /Disable/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('menuitem', { name: /regular user/ })).not.toBeInTheDocument()
})

test('disabling someone asks first', async () => {
  const user = await openMenu('dana@h3.local')
  await user.click(await screen.findByRole('menuitem', { name: /Disable/ }))
  const dialog = await screen.findByRole('alertdialog', { name: 'Disable dana@h3.local?' })
  expect(patches).toEqual([])
  await user.click(within(dialog).getByRole('button', { name: 'Disable' }))
  await waitFor(() => expect(patches).toEqual([{ id: 'u-2', body: { is_active: false } }]))
})
