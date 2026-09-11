import { QueryCache, QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'

const OFFLINE_TOAST = 'offline'

function isClientError(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    // A poll that fails after data was already on screen means we lost the
    // server, not that the page is broken. One toast, not one per query.
    onError: (error, query) => {
      if (error instanceof ApiError && error.status === 401) return
      if (query.state.data !== undefined) {
        toast.error('Lost contact with the server. Retrying…', { id: OFFLINE_TOAST, duration: Infinity })
      }
    },
    onSuccess: () => {
      toast.dismiss(OFFLINE_TOAST)
    },
  }),
  defaultOptions: {
    queries: {
      // 4xx won't get better by asking again; network blips might.
      retry: (count, error) => !isClientError(error) && count < 2,
      refetchOnWindowFocus: true,
      refetchIntervalInBackground: false,
      staleTime: 2_000,
    },
    mutations: { retry: 0 },
  },
})
