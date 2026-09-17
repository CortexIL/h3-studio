// Every query key in one place, so invalidation can't drift from the queries.
export const keys = {
  me: ['me'] as const,
  status: ['status'] as const,
  // Everything job-shaped, for invalidation.
  jobs: ['jobs'] as const,
  // The feed, at one page size. The size is part of the key so asking for more
  // is a new query rather than a silent refetch of the same one.
  jobList: (limit: number) => ['jobs', 'list', limit] as const,
  /** Every page size the feed has loaded, for writing to all of them at once. */
  jobListAll: ['jobs', 'list'] as const,
  job: (id: string) => ['jobs', id] as const,
  archive: (filters: { q?: string; preset?: string; mode?: string }) =>
    ['archive', filters] as const,
  archiveAll: ['archive'] as const,
  estimate: (payload: unknown) => ['estimate', payload] as const,
  admin: {
    users: ['admin', 'users'] as const,
    activity: ['admin', 'activity'] as const,
    status: ['admin', 'status'] as const,
    runs: ['admin', 'runs'] as const,
    keyState: ['admin', 'key-state'] as const,
    promptKeyState: ['admin', 'prompt-key-state'] as const,
  },
}
