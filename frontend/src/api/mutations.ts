import {
  type InfiniteData,
  type QueryClient,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query'
import { toast } from 'sonner'

import { errorMessage, isUnauthorized } from '@/lib/errors'
import { t, tn } from '@/i18n'

import { navigation, request, uploadWithProgress } from './client'
import { keys } from './keys'
import type { AdminUser, ArchivePage, Job, JobsPage, Me, NewJobsBody, Policy, PromptImprove, Role } from './types'

type JobsSnapshot = [readonly unknown[], JobsPage | undefined][]
type Snapshot = { jobs?: JobsSnapshot; archive?: [readonly unknown[], InfiniteData<ArchivePage> | undefined][] }

/**
 * Rewrite the feed wherever it is cached.
 *
 * The feed is keyed by its page size, so the cache can hold more than one copy
 * of it - the 300 it opened with and the 600 it grew to. Writing to one of them
 * by name would leave the other still showing a clip that has just gone.
 */
function patchJobs(qc: QueryClient, update: (jobs: Job[]) => Job[]): JobsSnapshot {
  const before = qc.getQueriesData<JobsPage>({ queryKey: keys.jobListAll })
  qc.setQueriesData<JobsPage>({ queryKey: keys.jobListAll }, (old) =>
    old ? { ...old, jobs: update(old.jobs) } : old,
  )
  return before
}

function restoreJobs(qc: QueryClient, snapshot?: JobsSnapshot) {
  snapshot?.forEach(([key, data]) => qc.setQueryData(key, data))
}

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
      await qc.cancelQueries({ queryKey: keys.jobListAll })
      return { jobs: patchJobs(qc, (jobs) => options.update(jobs, vars)) }
    },
    onError: (error, _vars, snapshot) => {
      restoreJobs(qc, snapshot?.jobs)
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
      toast.success(tn('toast.addedOne', 'toast.addedMany', r.count)),
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
    success: (r) => t(r.hidden ? 'toast.removedKept' : 'toast.removed'),
  })
}

export function useReorderQueue() {
  return useOptimisticJobs<string[], { reordered: number }>({
    mutationFn: (renderOrder) =>
      request<{ reordered: number }>('/api/jobs/order', { method: 'POST', json: { ids: renderOrder } }),
    // The feed sorts waiting clips by their queue position, so the optimistic
    // update rewrites the positions the way the server will: the moved clips take
    // the same set of positions they already held, handed out in the new order.
    // Everything else keeps its place while the poll catches up.
    update: (jobs, renderOrder) => {
      const byId = new Map(jobs.filter((j) => j.status === 'queued').map((j) => [j.id, j]))
      const moving = renderOrder.map((id) => byId.get(id)).filter((j): j is Job => Boolean(j))
      const positions = moving
        .map((j) => j.queue_position)
        .filter((p): p is number => p !== null)
        .sort((a, b) => a - b)
      const patched = new Map(moving.map((j, i) => [j.id, { ...j, queue_position: positions[i] ?? i }]))
      return jobs.map((job) => patched.get(job.id) ?? job)
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
    onSuccess: () => toast.success(t('toast.backInQueue')),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

export function useRunAgain() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => request<{ ok: boolean; job_id: string }>(`/api/jobs/${id}/again`, { method: 'POST' }),
    onSuccess: () => toast.success(t('toast.newTake')),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

/** Queue a fresh take of several clips at once. */
export function useUpscale() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, deliver }: { id: string; deliver: '2x' | '1080p' }) =>
      request<{ ok: boolean; job_id: string }>(`/api/jobs/${id}/upscale`, { method: 'POST', json: { deliver } }),
    onSuccess: (_r, { deliver }) => toast.success(t(deliver === '1080p' ? 'toast.upscale1080' : 'toast.upscale2x')),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

export function useRunAgainMany() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) =>
      request<{ queued: number }>('/api/jobs/again', { method: 'POST', json: { ids } }),
    onSuccess: (r) => toast.success(tn('toast.newTakesOne', 'toast.newTakesMany', r.queued)),
    onError: toastError,
    onSettled: () => refreshJobs(qc),
  })
}

/** Delete a finished clip and its file for good: from the feed and every archive page. */
export function useDeleteClip() {
  const qc = useQueryClient()
  return useMutation<unknown, unknown, string, Snapshot>({
    mutationFn: (id) => request(`/api/archive/${id}`, { method: 'DELETE' }),
    onMutate: (id) => dropClips(qc, [id]),
    onError: (error, _id, snapshot) => {
      restore(qc, snapshot)
      toastError(error)
    },
    onSuccess: () => toast.success(t('toast.clipDeleted')),
    onSettled: () => {
      refreshJobs(qc)
      void qc.invalidateQueries({ queryKey: keys.archiveAll })
    },
  })
}

/** As many ids as one delete request may carry; the server's own ceiling. */
const DELETE_CHUNK = 200

/** Take clips out of the feed and out of every archive page at once, keeping
 *  enough of both to put them back if the server refuses. */
async function dropClips(qc: QueryClient, ids: string[]): Promise<Snapshot> {
  const gone = new Set(ids)
  await qc.cancelQueries({ queryKey: keys.jobListAll })
  await qc.cancelQueries({ queryKey: keys.archiveAll })
  const jobs = patchJobs(qc, (list) => list.filter((j) => !gone.has(j.id)))
  const archive = qc.getQueriesData<InfiniteData<ArchivePage>>({ queryKey: keys.archiveAll })
  qc.setQueriesData<InfiniteData<ArchivePage>>({ queryKey: keys.archiveAll }, (old) =>
    old
      ? { ...old, pages: old.pages.map((p) => ({ ...p, clips: p.clips.filter((c) => !gone.has(c.id)) })) }
      : old,
  )
  return { jobs, archive }
}

function restore(qc: QueryClient, snapshot?: Snapshot) {
  restoreJobs(qc, snapshot?.jobs)
  snapshot?.archive?.forEach(([key, data]) => qc.setQueryData(key, data))
}

/**
 * Delete a whole selection, files and all.
 *
 * The server deletes what it can and says what it could not, so a selection
 * that only partly went is a number the user can see and act on, rather than
 * one error that hides how far it got.
 */
export function useDeleteClips() {
  const qc = useQueryClient()
  return useMutation<{ deleted: number; kept: number }, unknown, string[], Snapshot>({
    // Sent in batches the server is willing to take. Selecting everything and
    // pressing delete has to mean everything: a request that quietly dropped
    // the two hundred and first id is the bug this feature exists to avoid.
    mutationFn: async (ids) => {
      let deleted = 0
      let kept = 0
      for (let i = 0; i < ids.length; i += DELETE_CHUNK) {
        const batch = ids.slice(i, i + DELETE_CHUNK)
        const r = await request<{ deleted: number; kept: number }>('/api/archive/delete', {
          method: 'POST',
          json: { ids: batch },
        })
        deleted += r.deleted
        kept += r.kept
      }
      return { deleted, kept }
    },
    onMutate: (ids) => dropClips(qc, ids),
    onError: (error, _ids, snapshot) => {
      restore(qc, snapshot)
      toastError(error)
    },
    onSuccess: (r) => {
      if (r.deleted) toast.success(tn('toast.clipsDeletedOne', 'toast.clipsDeletedMany', r.deleted))
      if (r.kept) toast.error(tn('toast.clipsKeptOne', 'toast.clipsKeptMany', r.kept))
    },
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
      toast.success(t('toast.pictureUpdated'))
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
      toast.success(t('toast.pictureRemoved'))
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.users }),
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      request('/api/me/password', { method: 'POST', json: body }),
    onSuccess: () => toast.success(t('toast.passwordChanged')),
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
    onSuccess: (u) => toast.success(t('toast.created', { email: u.email })),
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

export function useSetSage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) =>
      request<{ sage: boolean }>('/api/admin/sage', { method: 'POST', json: { enabled } }),
    onError: toastError,
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.status }),
  })
}

export function useSetMaxPods() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (maxPods: number) =>
      request<{ max_pods: number }>('/api/admin/max-pods', { method: 'POST', json: { max_pods: maxPods } }),
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
    onSuccess: (r) => toast.success(t('toast.budgetSet', { amount: r.session_limit_usd.toFixed(2) })),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.status }),
  })
}

/** The prompt helper: the description rewritten the way the model's own pipeline writes it. */
export function useImprovePrompt() {
  return useMutation({
    mutationFn: (body: { prompt: string; mode: string; seconds: number; sound?: string; music?: string; has_start: boolean; has_end: boolean; keyframes: number }) =>
      request<PromptImprove>('/api/prompt/improve', { method: 'POST', json: body }),
    onError: toastError,
  })
}

export function useSavePromptKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (key: string) => request<{ ok: boolean; hint: string }>('/api/admin/prompt-key', { method: 'POST', json: { key } }),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.admin.promptKeyState }),
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
