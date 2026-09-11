// Every query key in one place, so invalidation can't drift from the queries.
export const keys = {
  me: ['me'] as const,
  status: ['status'] as const,
  jobs: ['jobs'] as const,
  job: (id: string) => ['jobs', id] as const,
  archive: (filters: { q?: string; preset?: string; mode?: string }) =>
    ['archive', filters] as const,
  archiveAll: ['archive'] as const,
  estimate: (payload: unknown) => ['estimate', payload] as const,
  admin: {
    users: ['admin', 'users'] as const,
    status: ['admin', 'status'] as const,
    runs: ['admin', 'runs'] as const,
    keyState: ['admin', 'key-state'] as const,
  },
}
