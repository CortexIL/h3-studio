import { useEffect } from 'react'

import { isRtl, useLocale } from '@/i18n'
import { useTheme } from '@/lib/theme'

/**
 * Puts the chosen language and theme on <html>, where CSS and the browser read
 * them: `dir` flips the layout, `lang` picks hyphenation and fonts, the `dark`
 * class selects the palette. index.html sets the same things before the first
 * paint so nothing flashes; this keeps them right afterwards.
 */
export function AppearanceEffect() {
  const locale = useLocale((s) => s.locale)
  const theme = useTheme((s) => s.theme)
  useEffect(() => {
    const root = document.documentElement
    root.lang = locale
    root.dir = isRtl(locale) ? 'rtl' : 'ltr'
  }, [locale])
  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', theme === 'dark')
    root.style.colorScheme = theme
  }, [theme])
  return null
}
