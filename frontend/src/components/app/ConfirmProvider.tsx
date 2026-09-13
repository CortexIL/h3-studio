import { useCallback, useRef, useState, type ReactNode } from 'react'

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/errors'
import { useT } from '@/i18n'

import { type Confirm, ConfirmContext, type ConfirmOptions } from './confirm'

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const t = useT()
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const resolver = useRef<((value: boolean) => void) | null>(null)

  const settle = useCallback((value: boolean) => {
    resolver.current?.(value)
    resolver.current = null
    setOptions(null)
    setPending(false)
    setError(null)
  }, [])

  const confirm = useCallback<Confirm>((next) => {
    // A second confirm while one is open answers the first with "no".
    resolver.current?.(false)
    setError(null)
    setPending(false)
    setOptions(next)
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve
    })
  }, [])

  const onConfirm = async () => {
    if (!options?.action) {
      settle(true)
      return
    }
    setPending(true)
    setError(null)
    try {
      await options.action()
      settle(true)
    } catch (err) {
      setPending(false)
      setError(errorMessage(err))
    }
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <AlertDialog
        open={options !== null}
        onOpenChange={(open) => {
          if (!open && !pending) settle(false)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{options?.title}</AlertDialogTitle>
            {options?.description ? (
              <AlertDialogDescription>{options.description}</AlertDialogDescription>
            ) : null}
          </AlertDialogHeader>
          {error ? (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>{options?.cancelLabel ?? t('common.cancel')}</AlertDialogCancel>
            <Button
              variant={options?.destructive ? 'destructive' : 'default'}
              disabled={pending}
              onClick={() => void onConfirm()}
            >
              {pending ? t('common.working') : (options?.confirmLabel ?? t('common.confirm'))}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ConfirmContext.Provider>
  )
}
