import { useCallback } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { create } from 'zustand'

/** The clips the viewer's arrows step through: whichever list the current page is showing. */
export const useViewerList = create<{ ids: string[]; setIds: (ids: string[]) => void }>()((set) => ({
  ids: [],
  setIds: (ids) => set({ ids }),
}))

interface ViewerHistoryState {
  clipViewer?: boolean
}

function withClip(prev: URLSearchParams, id: string | null) {
  const next = new URLSearchParams(prev)
  if (id) next.set('clip', id)
  else next.delete('clip')
  return next
}

/**
 * The clip viewer is a URL: ?clip=<id> on whatever page you are on, so a clip
 * can be linked to and the browser's Back button closes it.
 */
export function useClipViewer() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const openedHere = Boolean((location.state as ViewerHistoryState | null)?.clipViewer)

  const open = useCallback(
    (id: string) => setParams((prev) => withClip(prev, id), { state: { clipViewer: true } }),
    [setParams],
  )

  // Stepping between clips replaces the entry, so Back still closes the viewer.
  const go = useCallback(
    (id: string) => setParams((prev) => withClip(prev, id), { replace: true, state: location.state }),
    [setParams, location.state],
  )

  // If opening it added a history entry, closing goes back over it; otherwise
  // (a pasted link) it just drops the parameter.
  const close = useCallback(() => {
    if (openedHere) navigate(-1)
    else setParams((prev) => withClip(prev, null), { replace: true })
  }, [openedHere, navigate, setParams])

  return { id: params.get('clip'), open, go, close }
}
