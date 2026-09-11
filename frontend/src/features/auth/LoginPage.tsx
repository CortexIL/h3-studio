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

function describe(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return "That email and password don't match an account."
  }
  if (error instanceof ApiError && error.status === 429) {
    return 'Too many attempts. Wait five minutes, then try again.'
  }
  if (error instanceof ApiError && error.status === 0) return 'Could not reach the server.'
  return errorMessage(error)
}

export function LoginPage() {
  useDocumentTitle('Sign in')
  const [params] = useSearchParams()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password) {
      setError('Enter your email and password.')
      return
    }
    login.mutate(
      { email: email.trim(), password },
      {
        // A full load, so the server's page guard runs and the cache starts clean.
        onSuccess: () => navigation.replace(safeNext(params.get('next'))),
        onError: (err) => setError(describe(err)),
      },
    )
  }

  return (
    <main className="grid min-h-screen place-items-center px-4 py-10">
      <div className="grid w-full max-w-sm gap-6">
        <div className="grid justify-items-center gap-3 text-center">
          <img src={logo} alt="" className="size-12 rounded-xl" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Sign in to H3 Studio</h1>
            <p className="mt-1 text-muted-foreground">Use the account your administrator created for you.</p>
          </div>
        </div>
        <form noValidate onSubmit={onSubmit} className="grid gap-4 rounded-xl border bg-card p-6">
          <Field label="Email">
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
          <Field label="Password">
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
            {login.isPending ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
        <p className="text-center text-xs text-balance text-faint">No account? Ask your administrator. There is no public sign-up.</p>
      </div>
    </main>
  )
}
