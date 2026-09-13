import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'

import { ApiError, navigation } from '@/api/client'
import { useLogin } from '@/api/mutations'
import logo from '@/assets/logo.png'
import { Field } from '@/components/app/Field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { errorMessage } from '@/lib/errors'
import { useDocumentTitle } from '@/lib/hooks'
import { safeNext } from '@/lib/next'
import { AppearanceToggles } from '@/components/app/AppearanceToggles'
import { useT, type Key } from '@/i18n'

/** The dictionary key for a sign-in failure, or null when the server's own words are better. */
function describe(error: unknown): Key | null {
  if (error instanceof ApiError && error.status === 401) return 'login.bad'
  if (error instanceof ApiError && error.status === 429) return 'login.tooMany'
  if (error instanceof ApiError && error.status === 0) return 'login.unreachable'
  return null
}

export function LoginPage() {
  const t = useT()
  useDocumentTitle(t('login.docTitle'))
  const [params] = useSearchParams()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password) {
      setError(t('login.missing'))
      return
    }
    login.mutate(
      { email: email.trim(), password },
      {
        // A full load, so the server's page guard runs and the cache starts clean.
        onSuccess: () => navigation.replace(safeNext(params.get('next'))),
        onError: (err) => {
          const key = describe(err)
          setError(key ? t(key) : errorMessage(err))
        },
      },
    )
  }

  return (
    <main className="relative grid min-h-screen place-items-center px-4 py-10">
      <AppearanceToggles className="absolute top-3 end-3 flex items-center gap-0.5" />
      <div className="grid w-full max-w-sm gap-6">
        <div className="grid justify-items-center gap-3 text-center">
          <img src={logo} alt="" className="size-12 rounded-xl" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{t('login.title')}</h1>
            <p className="mt-1 text-muted-foreground">{t('login.subtitle')}</p>
          </div>
        </div>
        <form noValidate onSubmit={onSubmit} className="grid gap-4 rounded-xl border bg-card p-6">
          <Field label={t('login.email')}>
            {(props) => (
              <Input
                {...props}
                type="email"
                autoComplete="username"
                autoFocus
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value)
                  setError(null)
                }}
              />
            )}
          </Field>
          <Field label={t('login.password')}>
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value)
                  setError(null)
                }}
              />
            )}
          </Field>
          {error ? (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <Button type="submit" disabled={login.isPending} className="mt-1">
            {login.isPending ? t('login.submitting') : t('login.submit')}
          </Button>
        </form>
        <p className="text-center text-xs text-balance text-faint">{t('login.noAccount')}</p>
      </div>
    </main>
  )
}
