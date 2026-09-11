import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { expect, test } from 'vitest'

import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { AccountPage } from './AccountPage'

function withMe() {
  server.use(http.get('/api/me', () => HttpResponse.json({ id: '1', email: 'a@h3.local', role: 'user' })))
}

function passwordForm() {
  return within(screen.getByRole('button', { name: 'Change password' }).closest('form')!)
}

test('mismatched new passwords are caught without a request', async () => {
  withMe()
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />)
  const form = passwordForm()
  await user.type(form.getByLabelText('Current password'), 'old-passphrase')
  await user.type(form.getByLabelText('New password'), 'new-passphrase-1')
  await user.type(form.getByLabelText('Confirm new password'), 'new-passphrase-2')
  await user.click(form.getByRole('button', { name: 'Change password' }))
  expect(await form.findByText("The two new passwords don't match.")).toBeInTheDocument()
})

test('a short new password is caught', async () => {
  withMe()
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />)
  const form = passwordForm()
  await user.type(form.getByLabelText('Current password'), 'old-passphrase')
  await user.type(form.getByLabelText('New password'), 'short')
  await user.type(form.getByLabelText('Confirm new password'), 'short')
  await user.click(form.getByRole('button', { name: 'Change password' }))
  expect(await form.findByText('Use at least 8 characters.')).toBeInTheDocument()
})

test('a wrong current password is shown inline and nobody is signed out', async () => {
  withMe()
  server.use(
    http.post('/api/me/password', () =>
      HttpResponse.json({ detail: 'your current password is not right' }, { status: 400 }),
    ),
  )
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />)
  const form = passwordForm()
  await user.type(form.getByLabelText('Current password'), 'not-it-at-all')
  await user.type(form.getByLabelText('New password'), 'new-passphrase-1')
  await user.type(form.getByLabelText('Confirm new password'), 'new-passphrase-1')
  await user.click(form.getByRole('button', { name: 'Change password' }))
  expect(await form.findByRole('alert')).toHaveTextContent('Your current password is not right')
})

test('a successful change clears the form and says so', async () => {
  withMe()
  server.use(http.post('/api/me/password', () => HttpResponse.json({ ok: true })))
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />)
  const form = passwordForm()
  await user.type(form.getByLabelText('Current password'), 'old-passphrase')
  await user.type(form.getByLabelText('New password'), 'new-passphrase-1')
  await user.type(form.getByLabelText('Confirm new password'), 'new-passphrase-1')
  await user.click(form.getByRole('button', { name: 'Change password' }))
  expect(await screen.findByText(/Password changed/)).toBeInTheDocument()
  expect(form.getByLabelText('Current password')).toHaveValue('')
})

test('signing out everywhere asks first', async () => {
  withMe()
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />)
  await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
  expect(await screen.findByRole('alertdialog', { name: 'Sign out everywhere?' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
})
