import {
  type InfiniteData,
  type QueryClient,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query'
import { toast } from 'sonner'

import { errorMessage, isUnauthorized } from '@/lib/errors'

import { navigation, request, uploadWithProgress } from './client'
import { keys } from './keys'
import type { AdminUser, ArchivePage, Job, Me, NewJobsBody, Policy, Role } from './types'

type JobsData = { jobs: Job[] }
type Snapshot = { jobs?: JobsData; archive?: [readonly unknown[], InfiniteData<ArchivePage> | undefined][] }

// A 401 is already on its way to the sign-in page; a toast would just flash.
function toastError(error: unknown) {
  if (!isUnauthorized(error)) toast.error(errorMessage(error))
}

function refreshJobs(qc: QueryClient) {
  void qc.invalidateQueries({ queryKey: keys.jobs })
  void qc.invalidateQueries({ queryKey: keys.status })
}

/**
 * Shared shape for actions on the feed: update the list at once, put it back if
 * the server says no, and re-read it either way. cancelQueries first, or an
 * in-flight poll lands after the optimistic write and undoes it on screen.
 */
function useOptimisticJobs<TVars, TData = unknown>(options: {
  mutationFn: (vars: TVars) => Promise<TData>
  update: (jobs: Job[], vars: TVars) => Job[]
  success?: (data: TData, vars: TVars) => string | null
}) {
  const qc = useQueryClient()
  return useMutation<TData, unknown, TVars, Snapshot>({
    mutationFn: options.mutationFn,
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: keys.jobs, exact: true })
      const jobs = qc.getQueryData<JobsData>(keys.jobs)
      if (jobs) qc.setQueryData<JobsData>(keys.jobs, { jobs: options.update(jobs.jobs, vars) })
      return { jobs }
    },
    onError: (error, _vars, snapshot) => {
      if (snapshot?.jobs) qc.setQueryData(keys.jobs, snapshot.jobs)
      toastError(error)
    },
    onSuccess: (data, vars) => {
      const message = options.success?.(data, vars)
      if (message) toast.success(message)
    },
    onSettled: () => refreshJobs(qc),
  })
}

// ---------------- studio ----------------

export function useAddJobs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: NewJobsBody) =>
      request<{ created: string[]; count: number }>('/api/jobs', { method: 'POST', json: body }),
    onSuccess: (r) =>
      toast.success(r.count === 1 ? 'Added 1 clip to the queue' : `Added ${r.count} clips to the queue`),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

export function useCancelJob() {
  return useOptimisticJobs({
    mutationFn: (id: string) => request(`/api/jobs/${id}/cancel`, { method: 'POST' }),
    update: (jobs, id) => jobs.map((j) => (j.id === id ? { ...j, status: 'cancelled' } : j)),
    success: () => 'Cancelled',
  })
}

export function useRemoveFromFeed() {
  return useOptimisticJobs({
    mutationFn: (id: string) => request<{ ok: boolean; hidden: boolean }>(`/api/jobs/${id}`, { method: 'DELETE' }),
    update: (jobs, id) => jobs.filter((j) => j.id !== id),
    success: (r) => (r.hidden ? 'Removed from the list. The clip is still in your Archive.' : 'Removed'),
  })
}

export function useReorderQueue() {
  return useOptimisticJobs<string[], { reordered: number }>({
    mutationFn: (renderOrder) =>
      request<{ reordered: number }>('/api/jobs/order', { method: 'POST', json: { ids: renderOrder } }),
    // The feed reads newest first, the queue renders oldest first, so the ids
    // sent are the reverse of what the list shows. Only the queued slots move:
    // everything else keeps its place while the poll catches up.
    update: (jobs, renderOrder) => {
      const display = [...renderOrder].reverse()
      const byId = new Map(jobs.filter((j) => j.status === 'queued').map((j) => [j.id, j]))
      const ordered = display.map((id) => byId.get(id)).filter((j): j is Job => Boolean(j))
      let next = 0
      return jobs.map((job) => (job.status === 'queued' ? ordered[next++] ?? job : job))
    },
  })
}

export function useClearFinished() {
  return useOptimisticJobs<void, { removed: number }>({
    mutationFn: () => request<{ removed: number }>('/api/jobs/clear-finished', { method: 'POST' }),
    update: (jobs) => jobs.filter((j) => j.status === 'queued' || j.status === 'running'),
    success: (r) => (r.removed ? `Cleared ${r.removed} from the list. Finished clips stay in your Archive.` : null),
  })
}

export function useRetryJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => request(`/api/jobs/${id}/retry`, { method: 'POST' }),
    onSuccess: () => toast.success('Back in the queue'),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

export function useRunAgain() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => request<{ ok: boolean; job_id: string }>(`/api/jobs/${id}/again`, { method: 'POST' }),
    onSuccess: () => toast.success('Queued a new take'),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

/** Queue a fresh take of several clips at once. */
export function useRunAgainMany() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) =>
      request<{ queued: number }>('/api/jobs/again', { method: 'POST', json: { ids } }),
    onSuccess: (r) => toast.success(`Queued ${r.queued} new take${r.queued === 1 ? '' : 's'}`),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

/** Delete a finished clip and its file for good: from the feed and every archive page. */
export function useDeleteClip() {
  const qc = useQueryClient()
  return useMutation<unknown, unknown, string, Snapshot>({
    mutationFn: (id) => request(`/api/archive/${id}`, { method: 'DELETE' }),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: keys.jobs, exact: true })
      await qc.cancelQueries({ queryKey: keys.archiveAll })
      const jobs = qc.getQueryData<JobsData>(keys.jobs)
      if (jobs) qc.setQueryData<JobsData>(keys.jobs, { jobs: jobs.jobs.filter((j) => j.id !== id) })
      const archive = qc.getQueriesData<InfiniteData<ArchivePage>>({ queryKey: keys.archiveAll })
      qc.setQueriesData<InfiniteData<ArchivePage>>({ queryKey: keys.archiveAll }, (old) =>
        old
          ? { ...old, pages: old.pages.map((p) => ({ ...p, clips: p.clips.filter((c) => c.id !== id) })) }
          : old,
      )
      return { jobs, archive }
    },
    onError: (error, _id, snapshot) => {
      if (snapshot?.jobs) qc.setQueryData(keys.jobs, snapshot.jobs)
      snapshot?.archive?.forEach(([key, data]) => qc.setQueryData(key, data))
      toastError(error)
    },
    onSuccess: () => toast.success('Clip deleted'),
    onSettled: () => {
      refreshJobs(qc)
      void qc.invalidateQueries({ queryKey: keys.archiveAll })
    },
  })
}

// ---------------- account ----------------

export function useLogin() {
  // Errors belong inline on the form, so no toast here.
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      request<{ id: string; email: string; role: Role }>('/api/auth/login', { method: 'POST', json: body }),
  })
}

export function useUploadAvatar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => uploadWithProgress<Me>('/api/me/avatar', file),
    onSuccess: (me) => {
      qc.setQueryData(keys.me, me)
      toast.success('Profile picture updated')
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.users }),
  })
}

export function useRemoveAvatar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => request<Me>('/api/me/avatar', { method: 'DELETE' }),
    onSuccess: (me) => {
      qc.setQueryData(keys.me, me)
      toast.success('Profile picture removed')
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.users }),
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      request('/api/me/password', { method: 'POST', json: body }),
    onSuccess: () => toast.success('Password changed. Other devices have been signed out.'),
  })
}

function leaveToSignIn(qc: QueryClient) {
  qc.clear()
  navigation.replace('/login')
}

export function useSignOut() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => request('/api/auth/logout', { method: 'POST' }),
    onSettled: () => leaveToSignIn(qc),
  })
}

export function useSignOutEverywhere() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => request('/api/me/sign-out-everywhere', { method: 'POST' }),
    onSuccess: () => leaveToSignIn(qc),
    onError: toastError,
  })
}

// ---------------- admin ----------------

export function useCreateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { email: string; password: string; role: Role }) =>
      request<AdminUser>('/api/admin/users', { method: 'POST', json: body }),
    onSuccess: (u) => toast.success(`Created ${u.email}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.users }),
  })
}

export function useUpdateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...patch }: { id: string; is_active?: boolean; password?: string; role?: Role }) =>
      request<AdminUser>(`/api/admin/users/${id}`, { method: 'PATCH', json: patch }),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.users }),
  })
}

export function useSetPolicy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (policy: Policy) =>
      request<{ policy: Policy }>('/api/admin/policy', { method: 'POST', json: { policy } }),
    onError: toastError,
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.status }),
  })
}

export function useSetBudget() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (limit: number) =>
      request<{ session_limit_usd: number }>('/api/admin/budget', {
        method: 'POST',
        json: { session_limit_usd: limit },
      }),
    onSuccess: (r) => toast.success(`Session budget set to $${r.session_limit_usd.toFixed(2)}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.status }),
  })
}

export function useSaveRunpodKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (key: string) =>
      request<{ ok: boolean; hint: string; restart_required: boolean; note: string }>(
        '/api/admin/runpod-key',
        { method: 'POST', json: { key } },
      ),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.keyState }),
  })
}
