import { Camera, ImageUp, Trash2 } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'

import { ApiError } from '@/api/client'
import { useChangePassword, useRemoveAvatar, useSignOut, useSignOutEverywhere } from '@/api/mutations'
import { useMe } from '@/api/queries'
import { Avatar } from '@/components/app/Avatar'
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
import { useT } from '@/i18n'

import { AvatarCropDialog } from './AvatarCropDialog'

const MIN_PASSWORD = 8

function ProfileCard() {
  const t = useT()
  const { data: me } = useMe()
  const remove = useRemoveAvatar()
  const confirm = useConfirm()
  const input = useRef<HTMLInputElement>(null)
  const [cropping, setCropping] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)

  const choose = (file: File | undefined) => {
    if (!file) return
    // HEIC has no image/ type in some browsers; let the crop step decide whether it opens.
    if (!file.type.startsWith('image/') && !/\.(heic|heif)$/i.test(file.name)) {
      setError(t('account.notPicture'))
      return
    }
    setError(null)
    setCropping(file)
  }

  const onRemove = () =>
    void confirm({
      title: t('account.removeTitle'),
      description: t('account.removeDesc'),
      confirmLabel: t('common.remove'),
      action: () => remove.mutateAsync(),
    })

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('account.profile')}</CardTitle>
        <CardDescription>{t('account.profileDesc')}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-5">
        {me ? (
          <>
            <button
              type="button"
              onClick={() => input.current?.click()}
              className="group relative shrink-0 rounded-full outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              aria-label={t(me.avatar_url ? 'account.changePictureAria' : 'account.uploadPictureAria')}
            >
              <Avatar email={me.email} url={me.avatar_url} size="lg" alt={t('account.yourPicture')} />
              <span className="absolute inset-0 grid place-items-center rounded-full bg-black/55 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                <Camera className="size-5" />
              </span>
            </button>
            <div className="grid min-w-0 gap-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-base">{me.email}</span>
                <Badge variant={me.role === 'admin' ? 'default' : 'secondary'}>
                  {t(me.role === 'admin' ? 'user.administrator' : 'user.member')}
                </Badge>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => input.current?.click()}>
                  <ImageUp /> {t(me.avatar_url ? 'account.changePicture' : 'account.uploadPicture')}
                </Button>
                {me.avatar_url ? (
                  <Button size="sm" variant="ghost" onClick={onRemove} disabled={remove.isPending}>
                    <Trash2 /> {t('common.remove')}
                  </Button>
                ) : null}
              </div>
              {error ? (
                <p role="alert" className="text-xs text-destructive">
                  {error}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">{t('account.formats')}</p>
              )}
            </div>
            <input
              ref={input}
              type="file"
              accept="image/*,.heic,.heif"
              hidden
              data-testid="avatar-input"
              onChange={(e) => {
                choose(e.target.files?.[0])
                e.target.value = ''
              }}
            />
          </>
        ) : (
          <Skeleton className="h-20 w-72" />
        )}
      </CardContent>
      {cropping ? <AvatarCropDialog file={cropping} onClose={() => setCropping(null)} /> : null}
    </Card>
  )
}

type Errors = Partial<Record<'current' | 'next' | 'confirm' | 'form', string>>

function ChangePasswordCard() {
  const t = useT()
  const change = useChangePassword()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [errors, setErrors] = useState<Errors>({})

  const validate = (): Errors => {
    const found: Errors = {}
    if (!current) found.current = t('account.enterCurrent')
    if (next.length < MIN_PASSWORD) found.next = t('account.useAtLeast', { n: MIN_PASSWORD })
    else if (next === current) found.next = t('account.differentPassword')
    if (confirmation !== next) found.confirm = t('account.mismatch')
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
                ? t('login.tooMany')
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
        <CardTitle>{t('account.changePassword')}</CardTitle>
        <CardDescription>{t('account.changePasswordDesc')}</CardDescription>
      </CardHeader>
      <CardContent>
        <form noValidate onSubmit={onSubmit} className="grid max-w-md gap-4">
          <Field label={t('account.currentPassword')} error={errors.current}>
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
          <Field label={t('account.newPassword')} error={errors.next} hint={t('account.atLeast', { n: MIN_PASSWORD })}>
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
          <Field label={t('account.confirmPassword')} error={errors.confirm}>
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
              {change.isPending ? t('common.saving') : t('account.changePassword')}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function SessionsCard() {
  const t = useT()
  const confirm = useConfirm()
  const signOut = useSignOut()
  const everywhere = useSignOutEverywhere()
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('account.sessions')}</CardTitle>
        <CardDescription>{t('account.sessionsDesc')}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-3">
        <Button variant="outline" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
          {t('user.signOut')}
        </Button>
        <Button
          variant="destructive"
          onClick={() =>
            void confirm({
              title: t('account.signOutEverywhereTitle'),
              description: t('account.signOutEverywhereDesc'),
              confirmLabel: t('account.signOutEverywhere'),
              destructive: true,
              action: () => everywhere.mutateAsync(),
            })
          }
        >
          {t('account.signOutEverywhere')}
        </Button>
      </CardContent>
    </Card>
  )
}

export function AccountPage() {
  const t = useT()
  useDocumentTitle(t('account.docTitle'))
  return (
    <Page title={t('account.title')} description={t('account.desc')}>
      <ProfileCard />
      <ChangePasswordCard />
      <SessionsCard />
    </Page>
  )
}
