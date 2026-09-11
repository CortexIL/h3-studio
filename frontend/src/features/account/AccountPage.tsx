import { useState, type FormEvent } from 'react'

import { ApiError } from '@/api/client'
import { useChangePassword, useSignOut, useSignOutEverywhere } from '@/api/mutations'
import { useMe } from '@/api/queries'
import { useConfirm } from '@/components/app/confirm'
import { Field } from '@/components/app/Field'
import { Page } from '@/components/app/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage } from '@/lib/errors'
import { useDocumentTitle } from '@/lib/hooks'

const MIN_PASSWORD = 8

function ProfileCard() {
  const { data: me } = useMe()
  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>You sign in with this email. Only an administrator can change it.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3">
        {me ? (
          <>
            <span className="text-base">{me.email}</span>
            <Badge variant={me.role === 'admin' ? 'default' : 'secondary'}>
              {me.role === 'admin' ? 'Administrator' : 'Member'}
            </Badge>
          </>
        ) : (
          <Skeleton className="h-6 w-56" />
        )}
      </CardContent>
    </Card>
  )
}

type Errors = Partial<Record<'current' | 'next' | 'confirm' | 'form', string>>

function ChangePasswordCard() {
  const change = useChangePassword()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [errors, setErrors] = useState<Errors>({})

  const validate = (): Errors => {
    const found: Errors = {}
    if (!current) found.current = 'Enter your current password.'
    if (next.length < MIN_PASSWORD) found.next = `Use at least ${MIN_PASSWORD} characters.`
    else if (next === current) found.next = 'Choose a password different from the current one.'
    if (confirmation !== next) found.confirm = "The two new passwords don't match."
    return found
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const found = validate()
    setErrors(found)
    if (Object.keys(found).length) return
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent('')
          setNext('')
          setConfirmation('')
        },
        onError: (err) =>
          setErrors({
            form:
              err instanceof ApiError && err.status === 429
                ? 'Too many attempts. Wait five minutes, then try again.'
                : errorMessage(err),
          }),
      },
    )
  }

  const clear = (key: keyof Errors) =>
    setErrors((prev) => {
      const next = { ...prev }
      delete next[key]
      delete next.form
      return next
    })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change password</CardTitle>
        <CardDescription>You stay signed in here. Every other device is signed out.</CardDescription>
      </CardHeader>
      <CardContent>
        <form noValidate onSubmit={onSubmit} className="grid max-w-md gap-4">
          <Field label="Current password" error={errors.current}>
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(e) => {
                  setCurrent(e.target.value)
                  clear('current')
                }}
              />
            )}
          </Field>
          <Field label="New password" error={errors.next} hint={`At least ${MIN_PASSWORD} characters.`}>
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(e) => {
                  setNext(e.target.value)
                  clear('next')
                }}
              />
            )}
          </Field>
          <Field label="Confirm new password" error={errors.confirm}>
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="new-password"
                value={confirmation}
                onChange={(e) => {
                  setConfirmation(e.target.value)
                  clear('confirm')
                }}
              />
            )}
          </Field>
          {errors.form ? (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {errors.form}
            </p>
          ) : null}
          <div>
            <Button type="submit" disabled={change.isPending}>
              {change.isPending ? 'Saving…' : 'Change password'}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function SessionsCard() {
  const confirm = useConfirm()
  const signOut = useSignOut()
  const everywhere = useSignOutEverywhere()
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sessions</CardTitle>
        <CardDescription>
          Signed in somewhere you shouldn't be, like a shared computer? Sign out everywhere at once.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-3">
        <Button variant="outline" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
          Sign out
        </Button>
        <Button
          variant="destructive"
          onClick={() =>
            void confirm({
              title: 'Sign out everywhere?',
              description: 'Every device signed in to your account is signed out, including this one.',
              confirmLabel: 'Sign out everywhere',
              destructive: true,
              action: () => everywhere.mutateAsync(),
            })
          }
        >
          Sign out everywhere
        </Button>
      </CardContent>
    </Card>
  )
}

export function AccountPage() {
  useDocumentTitle('Account')
  return (
    <Page title="Account" description="Your sign-in details and where you're signed in.">
      <ProfileCard />
      <ChangePasswordCard />
      <SessionsCard />
    </Page>
  )
}
