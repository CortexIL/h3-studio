import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

export type Layout = 'studio' | 'classic'

interface LayoutState {
  layout: Layout
  setLayout: (layout: Layout) => void
  toggle: () => void
}

/** Which studio a person sees.
 *
 * 'studio' is the default: the Create panel with everything the beta added and
 * the plain-language names. 'classic' is the Create panel as it was before the
 * beta, with the old names, for anyone who preferred it. Remembered per browser,
 * like the language and the theme.
 */
export const useLayout = create<LayoutState>()(
  persist(
    (set, get) => ({
      layout: 'studio',
      setLayout: (layout) => set({ layout }),
      toggle: () => set({ layout: get().layout === 'classic' ? 'studio' : 'classic' }),
    }),
    { name: 'h3.layout', storage: createJSONStorage(() => localStorage) },
  ),
)
