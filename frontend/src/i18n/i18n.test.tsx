import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, expect, test } from 'vitest'

import { AppearanceEffect } from '@/components/app/AppearanceEffect'
import { AppearanceToggles } from '@/components/app/AppearanceToggles'
import { AppShell } from '@/components/app/AppShell'
import { BetaPage } from '@/features/beta/BetaPage'
import { BETA_FEATURES, betaFeatures } from '@/features/beta/features'
import { BETA_FEATURES_HE } from '@/features/beta/features.he'
import { useTheme } from '@/lib/theme'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { en, type Key } from './en'
import { he } from './he'
import { t, useLocale } from './index'

afterEach(() => {
  useLocale.setState({ locale: 'en' })
  useTheme.setState({ theme: 'dark' })
})

const placeholders = (text: string) => new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))

test('every English key has a Hebrew line, and Hebrew never invents a placeholder', () => {
  for (const key of Object.keys(en) as Key[]) {
    expect(he[key], key).toBeTruthy()
    // Hebrew may drop a number when the word carries it ("קליפ אחד"), never add one.
    for (const name of placeholders(he[key])) expect(placeholders(en[key]), key).toContain(name)
  }
})

test('t fills placeholders and follows the chosen language', () => {
  expect(t('pod.queue', { queued: 2, running: 1 })).toBe('2 in the shared queue · 1 generating')
  useLocale.setState({ locale: 'he' })
  expect(t('nav.studio')).toBe('סטודיו')
  expect(t('pod.queue', { queued: 2, running: 1 })).toContain('2')
})

test('the Hebrew beta catalogue matches the English one feature for feature', () => {
  for (const f of BETA_FEATURES) {
    const h = BETA_FEATURES_HE[f.id]
    expect(h, f.id).toBeDefined()
    expect(h!.howTo, f.id).toHaveLength(f.howTo.length)
    expect(h!.limits, f.id).toHaveLength(f.limits.length)
  }
  // Status and the GPU check come from the English entry, whatever the language.
  const merged = betaFeatures('he')
  expect(merged.map((f) => f.status)).toEqual(BETA_FEATURES.map((f) => f.status))
  expect(merged.map((f) => f.verified)).toEqual(BETA_FEATURES.map((f) => f.verified))
  expect(merged.find((f) => f.id === 'sound')?.title).toBe('הכוונת סאונד')
})

test('switching the language flips the page direction and translates the shell', async () => {
  server.use(
    http.get('/api/me', () => HttpResponse.json({ id: '1', email: 'a@h3.local', role: 'user' })),
    http.get('/api/status', () =>
      HttpResponse.json({ pod: { state: 'off' }, queue: { total_queued: 0, total_running: 0 }, config: { presets: {} } }),
    ),
  )
  const user = userEvent.setup()
  renderWithProviders(
    <>
      <AppearanceEffect />
      <AppShell />
    </>,
  )
  expect(await screen.findByRole('link', { name: 'Studio' })).toBeInTheDocument()
  expect(document.documentElement.dir).toBe('ltr')

  await user.click(screen.getAllByRole('button', { name: 'Switch to עברית' })[0]!)
  expect(await screen.findByRole('link', { name: 'סטודיו' })).toBeInTheDocument()
  expect(document.documentElement.dir).toBe('rtl')
  expect(document.documentElement.lang).toBe('he')

  await user.click(screen.getAllByRole('button', { name: 'מעבר ל-English' })[0]!)
  expect(await screen.findByRole('link', { name: 'Studio' })).toBeInTheDocument()
  expect(document.documentElement.dir).toBe('ltr')
})

test('the theme button switches between dark and light', async () => {
  const user = userEvent.setup()
  renderWithProviders(
    <>
      <AppearanceEffect />
      <AppearanceToggles />
    </>,
  )
  expect(document.documentElement.classList.contains('dark')).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Switch to light mode' }))
  expect(document.documentElement.classList.contains('dark')).toBe(false)
  expect(document.documentElement.style.colorScheme).toBe('light')
  await user.click(screen.getByRole('button', { name: 'Switch to dark mode' }))
  expect(document.documentElement.classList.contains('dark')).toBe(true)
})

test('the Beta page reads in Hebrew when Hebrew is chosen', async () => {
  useLocale.setState({ locale: 'he' })
  renderWithProviders(<BetaPage />)
  expect(await screen.findByRole('heading', { level: 1, name: 'בטא' })).toBeInTheDocument()
  expect(screen.getAllByText('הכוונת סאונד').length).toBeGreaterThan(0)
  expect(screen.getAllByText('מה המודל עושה').length).toBeGreaterThan(0)
  expect(screen.queryByText('What the model does')).not.toBeInTheDocument()
})
