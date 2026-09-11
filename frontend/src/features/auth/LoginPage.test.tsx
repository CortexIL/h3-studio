import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { expect, test, vi } from 'vitest'

import { navigation } from '@/api/client'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { LoginPage } from './LoginPage'

async function fillAndSubmit(email = 'a@h3.local', password = 'passphrase-1') {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  return user
}

test('a wrong password shows a plain message, and typing clears it', async () => {
  server.use(http.post('/api/auth/login', () => HttpResponse.json({ detail: 'wrong email or password' }, { status: 401 })))
  renderWithProviders(<LoginPage />, { route: '/login' })
  const user = await fillAndSubmit()
  expect(await screen.findByRole('alert')).toHaveTextContent("don't match an account")
  await user.type(screen.getByLabelText('Password'), 'x')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('too many attempts gets its own message', async () => {
  server.use(http.post('/api/auth/login', () => HttpResponse.json({ detail: 'x' }, { status: 429 })))
  renderWithProviders(<LoginPage />, { route: '/login' })
  await fillAndSubmit()
  expect(await screen.findByRole('alert')).toHaveTextContent('Wait five minutes')
})

test('empty fields are caught before any request', async () => {
  renderWithProviders(<LoginPage />, { route: '/login' })
  await userEvent.setup().click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Enter your email and password')
})

test('success returns to ?next= when it is a same-origin path', async () => {
  const replace = vi.spyOn(navigation, 'replace').mockImplementation(() => {})
  server.use(http.post('/api/auth/login', () => HttpResponse.json({ id: '1', email: 'a@h3.local', role: 'user' })))
  renderWithProviders(<LoginPage />, { route: '/login?next=/archive' })
  await fillAndSubmit()
  await vi.waitFor(() => expect(replace).toHaveBeenCalledWith('/archive'))
})

test('an off-site ?next= is ignored', async () => {
  const replace = vi.spyOn(navigation, 'replace').mockImplementation(() => {})
  server.use(http.post('/api/auth/login', () => HttpResponse.json({ id: '1', email: 'a@h3.local', role: 'user' })))
  renderWithProviders(<LoginPage />, { route: '/login?next=//evil.example' })
  await fillAndSubmit()
  await vi.waitFor(() => expect(replace).toHaveBeenCalledWith('/'))
})
