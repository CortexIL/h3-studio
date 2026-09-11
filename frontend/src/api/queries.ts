import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { request } from './client'
import { keys } from './keys'
import type {
  AdminStatus,
  AdminUser,
  ArchivePage,
  Estimate,
  Job,
  KeyState,
  Me,
  NewJobsBody,
  Run,
  Status,
} from './types'

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => request<Me>('/api/me'),
    staleTime: Infinity,
  })
}

export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: () => request<Status>('/api/status'),
    refetchInterval: 3_000,
  })
}

const isActive = (job: Job) => job.status === 'queued' || job.status === 'running'

export function useJobs() {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: keys.jobs,
    queryFn: () => request<{ jobs: Job[] }>('/api/jobs'),
    // Poll fast only while something is actually moving.
    refetchInterval: (query) => (query.state.data?.jobs.some(isActive) ? 2_500 : 15_000),
  })
  // When a clip finishes, the Archive is out of date: mark it stale so it
  // refetches the moment someone looks at it.
  const done = query.data?.jobs.filter((j) => j.status === 'done').map((j) => j.id).join(',')
  const seen = useRef(done)
  useEffect(() => {
    if (done !== undefined && seen.current !== undefined && done !== seen.current) {
      void qc.invalidateQueries({ queryKey: keys.archiveAll })
    }
    seen.current = done
  }, [done, qc])
  return query
}

export function useJob(id: string | null) {
  return useQuery({
    queryKey: keys.job(id ?? ''),
    queryFn: () => request<Job>(`/api/jobs/${id}`),
    enabled: id !== null,
    refetchInterval: (query) => {
      const job = query.state.data
      return job && !isActive(job) ? false : 2_500
    },
  })
}

export interface ArchiveFilters {
  q?: string
  preset?: string
  mode?: string
}

function cleanFilters(filters: ArchiveFilters): ArchiveFilters {
  const out: ArchiveFilters = {}
  if (filters.q?.trim()) out.q = filters.q.trim()
  if (filters.preset) out.preset = filters.preset
  if (filters.mode) out.mode = filters.mode
  return out
}

export function useArchive(filters: ArchiveFilters) {
  const clean = cleanFilters(filters)
  return useInfiniteQuery({
    queryKey: keys.archive(clean),
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams(clean as Record<string, string>)
      if (pageParam) params.set('cursor', pageParam)
      return request<ArchivePage>(`/api/archive?${params.toString()}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
}

export function useEstimate(body: NewJobsBody | null) {
  return useQuery({
    queryKey: keys.estimate(body),
    queryFn: () => request<Estimate>('/api/estimate', { method: 'POST', json: body }),
    enabled: body !== null && body.prompts.trim() !== '',
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  })
}

// ---------------- admin ----------------

export function useAdminUsers() {
  return useQuery({
    queryKey: keys.admin.users,
    queryFn: () => request<{ users: AdminUser[] }>('/api/admin/users'),
    refetchInterval: 30_000,
  })
}

export function useAdminStatus() {
  return useQuery({
    queryKey: keys.admin.status,
    queryFn: () => request<AdminStatus>('/api/admin/status'),
    refetchInterval: 5_000,
  })
}

export function useAdminRuns() {
  return useQuery({
    queryKey: keys.admin.runs,
    queryFn: () => request<{ runs: Run[]; total_cost_usd: number }>('/api/admin/runs'),
    refetchInterval: 15_000,
  })
}

export function useKeyState() {
  return useQuery({
    queryKey: keys.admin.keyState,
    queryFn: () => request<KeyState>('/api/admin/key-state'),
  })
}
