import { useMe } from '@/api/queries'

/** The name to credit when someone else made this clip (the part of their email before the @), else null. */
export function useMadeByOther(madeBy: string | null): string | null {
  const me = useMe().data?.email
  return madeBy && madeBy !== me ? (madeBy.split('@')[0] ?? null) : null
}
