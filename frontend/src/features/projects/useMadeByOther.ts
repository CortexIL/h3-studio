import { useMe } from '@/api/queries'

/** The name to credit when someone other than `me` made this clip (the part of their email before the @), else null. */
export function madeByOther(madeBy: string | null, me: string | undefined): string | null {
  return madeBy && madeBy !== me ? (madeBy.split('@')[0] ?? null) : null
}

/** `madeByOther` for the signed-in person. */
export function useMadeByOther(madeBy: string | null): string | null {
  return madeByOther(madeBy, useMe().data?.email)
}
