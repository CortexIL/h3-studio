import { useCallback } from 'react'
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import { en, type Key } from './en'
import { he } from './he'

export type Locale = 'en' | 'he'
export type { Key }

const DICT: Record<Locale, Record<Key, string>> = { en, he }

/** Hebrew for a Hebrew browser, English otherwise - until the person chooses. */
function guess(): Locale {
  try {
    return (navigator.language || '').toLowerCase().startsWith('he') ? 'he' : 'en'
  } catch {
    return 'en'
  }
}

interface LocaleState {
  locale: Locale
  setLocale: (locale: Locale) => void
}

/** The chosen language, remembered per browser. */
export const useLocale = create<LocaleState>()(
  persist((set) => ({ locale: guess(), setLocale: (locale) => set({ locale }) }), {
    name: 'h3.locale',
    storage: createJSONStorage(() => localStorage),
  }),
)

export const isRtl = (locale: Locale) => locale === 'he'

/** A string in the current language, with `{name}` placeholders filled in. */
export function t(key: Key, vars?: Record<string, string | number>): string {
  const locale = useLocale.getState().locale
  let text: string = DICT[locale][key] ?? en[key] ?? key
  if (vars) {
    for (const [name, value] of Object.entries(vars)) text = text.split(`{${name}}`).join(String(value))
  }
  return text
}

/** One of two strings by count: exactly one, or anything else. */
export function tn(one: Key, many: Key, n: number, vars?: Record<string, string | number>): string {
  return t(n === 1 ? one : many, { n, ...vars })
}

/** `t` for components: re-renders them when the language changes. */
export function useT() {
  const locale = useLocale((s) => s.locale)
  return useCallback(
    (key: Key, vars?: Record<string, string | number>) => t(key, vars),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the locale is the dependency: t reads it
    [locale],
  )
}

/** The BCP 47 tag for Intl formatting. */
export function intlLocale(): string {
  return useLocale.getState().locale === 'he' ? 'he-IL' : 'en-US'
}
