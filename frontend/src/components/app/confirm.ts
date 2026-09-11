import { createContext, type ReactNode, useContext } from 'react'

export interface ConfirmOptions {
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  /**
   * Run this before closing. The dialog stays open with the confirm button
   * pending, and shows the error inline if it fails - so a destructive action
   * never just vanishes and leaves the user wondering whether it happened.
   */
  action?: () => Promise<unknown>
}

export type Confirm = (options: ConfirmOptions) => Promise<boolean>

export const ConfirmContext = createContext<Confirm | null>(null)

export function useConfirm(): Confirm {
  const confirm = useContext(ConfirmContext)
  if (!confirm) throw new Error('useConfirm must be used inside <ConfirmProvider>')
  return confirm
}
