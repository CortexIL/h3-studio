import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, test, vi } from 'vitest'

import { server } from '@/test/server'

import { ApiError, navigation, request } from './client'

afterEach(() => {
  window.history.replaceState(null, '', '/')
})

describe('request', () => {
  test('returns parsed JSON on success', async () => {
    server.use(http.get('/api/me', () => HttpResponse.json({ id: '1', email: 'a@b.c', role: 'user' })))
    await expect(request('/api/me')).resolves.toEqual({ id: '1', email: 'a@b.c', role: 'user' })
  })

  test('surfaces the server detail as the error message', async () => {
    server.use(http.post('/api/jobs', () => HttpResponse.json({ detail: 'no prompts given' }, { status: 400 })))
    const err = await request('/api/jobs', { method: 'POST', json: {} }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(400)
    expect((err as ApiError).message).toBe('no prompts given')
  })

  test('joins FastAPI validation messages', async () => {
    server.use(
      http.get('/api/archive', () =>
        HttpResponse.json(
          { detail: [{ msg: 'String should have at most 200 characters' }] },
          { status: 422 },
        ),
      ),
    )
    await expect(request('/api/archive')).rejects.toThrow('String should have at most 200 characters')
  })

  test('a 401 sends the user to sign in, remembering where they were', async () => {
    window.history.replaceState(null, '', '/archive?q=car')
    const toLogin = vi.spyOn(navigation, 'toLogin').mockImplementation(() => {})
    server.use(http.get('/api/jobs', () => HttpResponse.json({ detail: 'not signed in' }, { status: 401 })))
    await expect(request('/api/jobs')).rejects.toBeInstanceOf(ApiError)
    expect(toLogin).toHaveBeenCalledWith('/login?next=%2Farchive%3Fq%3Dcar')
  })

  test('several 401s redirect only once', async () => {
    window.history.replaceState(null, '', '/archive')
    const toLogin = vi.spyOn(navigation, 'toLogin').mockImplementation(() => {})
    server.use(http.get('/api/jobs', () => HttpResponse.json({ detail: 'x' }, { status: 401 })))
    await Promise.allSettled([request('/api/jobs'), request('/api/jobs'), request('/api/jobs')])
    expect(toLogin).toHaveBeenCalledTimes(1)
  })

  test('a wrong password on sign-in is an error for the form, not a redirect', async () => {
    const toLogin = vi.spyOn(navigation, 'toLogin').mockImplementation(() => {})
    server.use(
      http.post('/api/auth/login', () =>
        HttpResponse.json({ detail: 'wrong email or password' }, { status: 401 }),
      ),
    )
    await expect(request('/api/auth/login', { method: 'POST', json: {} })).rejects.toThrow(
      'wrong email or password',
    )
    expect(toLogin).not.toHaveBeenCalled()
  })
})
