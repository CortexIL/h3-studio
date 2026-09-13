import { KeyRound, Loader2, MoreHorizontal, Shield, ShieldOff, UserCheck, UserPlus, UserX, Wand2, WifiOff } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { useCreateUser, useUpdateUser } from '@/api/mutations'
import { useAdminUsers, useMe } from '@/api/queries'
import type { AdminUser, Role } from '@/api/types'
import { Avatar } from '@/components/app/Avatar'
import { useConfirm } from '@/components/app/confirm'
import { SecretReveal } from '@/components/app/CopyButton'
import { EmptyState } from '@/components/app/EmptyState'
import { Field } from '@/components/app/Field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorMessage } from '@/lib/errors'
import { fmtBytes, fmtRelative, fmtWhen } from '@/lib/format'
import { generatePassword } from '@/lib/password'
import { cn } from '@/lib/utils'
import { tn, useT } from '@/i18n'

import { Panel } from './Panel'

const MIN_PASSWORD = 8
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function PasswordField({
  value,
  onChange,
  error,
}: {
  value: string
  onChange: (value: string) => void
  error: string | null
}) {
  const t = useT()
  return (
    <Field label={t('admin.password')} error={error} hint={t('admin.passwordHint', { n: MIN_PASSWORD })}>
      {(props) => (
        <div className="flex gap-2">
          <Input
            {...props}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            autoComplete="new-password"
            spellCheck={false}
            className="h-9 min-w-0 flex-1 font-mono"
          />
          <Button type="button" variant="outline" className="h-9" onClick={() => onChange(generatePassword())}>
            <Wand2 /> {t('admin.generate')}
          </Button>
        </div>
      )}
    </Field>
  )
}

function ResetPasswordDialog({ user, isSelf, onClose }: { user: AdminUser; isSelf: boolean; onClose: () => void }) {
  const t = useT()
  const update = useUpdateUser()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (password.length < MIN_PASSWORD) {
      setError(t('account.useAtLeast', { n: MIN_PASSWORD }))
      return
    }
    update.mutate(
      { id: user.id, password },
      {
        onSuccess: () => {
          setDone(true)
          toast.success(t('admin.passwordChangedFor', { email: user.email }))
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t(done ? 'admin.resetDoneTitle' : 'admin.resetTitle')}</DialogTitle>
          <DialogDescription>
            {done
              ? isSelf
                ? t('admin.resetDoneSelf')
                : t('admin.resetDoneOther', { email: user.email })
              : isSelf
                ? t('admin.resetSelf')
                : t('admin.resetOther', { email: user.email })}
          </DialogDescription>
        </DialogHeader>
        {done ? (
          <>
            <SecretReveal value={password} />
            <DialogFooter>
              <Button onClick={onClose}>{t('common.done')}</Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="grid gap-5">
            <PasswordField
              value={password}
              onChange={(v) => {
                setPassword(v)
                setError(null)
              }}
              error={error}
            />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={onClose}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={update.isPending}>
                {update.isPending ? <Loader2 className="animate-spin" /> : null}
                {t('admin.setPassword')}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const t = useT()
  const create = useCreateUser()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('user')
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({})
  const [created, setCreated] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const address = email.trim()
    const next = {
      ...(EMAIL.test(address) ? {} : { email: t('admin.badEmail') }),
      ...(password.length >= MIN_PASSWORD ? {} : { password: t('account.useAtLeast', { n: MIN_PASSWORD }) }),
    }
    setErrors(next)
    if (next.email || next.password) return
    create.mutate(
      { email: address, password, role },
      {
        onSuccess: (u) => setCreated(u.email),
        onError: (err) => setErrors({ form: errorMessage(err) }),
      },
    )
  }

  const details = created ? t('admin.details', { email: created, password, url: `${window.location.origin}/login` }) : ''

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t(created ? 'admin.createdTitle' : 'admin.createTitle')}</DialogTitle>
          <DialogDescription>
            {t(created ? 'admin.createdDesc' : 'admin.createDesc')}
          </DialogDescription>
        </DialogHeader>
        {created ? (
          <>
            <SecretReveal value={details} copyText={details} label={t('admin.copyAll')} />
            <DialogFooter>
              <Button onClick={onClose}>{t('common.done')}</Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="grid gap-5" noValidate>
            <Field label={t('admin.email')} error={errors.email}>
              {(props) => (
                <Input
                  {...props}
                  type="email"
                  autoComplete="off"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value)
                    setErrors((x) => ({ ...x, email: undefined, form: undefined }))
                  }}
                  className="h-9"
                  autoFocus
                />
              )}
            </Field>
            <Field label={t('admin.role')}>
              {(props) => (
                <Select value={role} onValueChange={(v) => setRole(v as Role)}>
                  <SelectTrigger id={props.id} className="h-9 w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">{t('admin.roleUser')}</SelectItem>
                    <SelectItem value="admin">{t('admin.roleAdmin')}</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </Field>
            <PasswordField
              value={password}
              onChange={(v) => {
                setPassword(v)
                setErrors((x) => ({ ...x, password: undefined, form: undefined }))
              }}
              error={errors.password ?? null}
            />
            {errors.form ? (
              <p role="alert" className="text-sm text-destructive">
                {errors.form}
              </p>
            ) : null}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={onClose}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? <Loader2 className="animate-spin" /> : null}
                {t('admin.createUser')}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

export function UsersTab() {
  const users = useAdminUsers()
  const me = useMe().data
  const t = useT()
  const update = useUpdateUser()
  const confirm = useConfirm()
  const [creating, setCreating] = useState(false)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)

  const changeRole = (u: AdminUser, role: Role) =>
    void confirm({
      title: t(role === 'admin' ? 'admin.makeAdminTitle' : 'admin.makeUserTitle', { email: u.email }),
      description: t(role === 'admin' ? 'admin.makeAdminDesc' : 'admin.makeUserDesc'),
      confirmLabel: t(role === 'admin' ? 'admin.makeAdmin' : 'admin.makeUser'),
      action: () => update.mutateAsync({ id: u.id, role }).then(() => void toast.success(t(role === 'admin' ? 'admin.nowAdmin' : 'admin.nowUser', { email: u.email }))),
    })

  const changeActive = (u: AdminUser, active: boolean) => {
    if (active) {
      update.mutate(
        { id: u.id, is_active: true },
        { onSuccess: () => toast.success(t('admin.canSignIn', { email: u.email })), onError: (err) => toast.error(errorMessage(err)) },
      )
      return
    }
    void confirm({
      title: t('admin.disableTitle', { email: u.email }),
      description: t('admin.disableDesc'),
      confirmLabel: t('admin.disable'),
      destructive: true,
      action: () => update.mutateAsync({ id: u.id, is_active: false }).then(() => void toast.success(t('admin.isDisabled', { email: u.email }))),
    })
  }

  const list = users.data?.users ?? []

  return (
    <Panel
      title={t('admin.users')}
      description={users.data ? tn('admin.accountsOne', 'admin.accountsMany', list.length) : undefined}
      actions={
        <Button onClick={() => setCreating(true)} className="shrink-0">
          <UserPlus /> {t('admin.createUser')}
        </Button>
      }
      className="p-0 [&>header]:p-5 [&>header]:pb-0"
    >
      {users.isPending ? (
        <div className="grid gap-2 p-5" aria-label={t('admin.loadingUsers')}>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : !users.data ? (
        <EmptyState
          icon={WifiOff}
          title={t('admin.loadUsersFailed')}
          action={
            <Button size="sm" variant="outline" onClick={() => void users.refetch()}>
              {t('common.tryAgain')}
            </Button>
          }
        />
      ) : (
        <Table className="text-sm">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="ps-5">{t('admin.col.email')}</TableHead>
              <TableHead>{t('admin.col.role')}</TableHead>
              <TableHead>{t('admin.col.status')}</TableHead>
              <TableHead className="text-end">{t('admin.col.clips')}</TableHead>
              <TableHead className="text-end">{t('admin.col.queue')}</TableHead>
              <TableHead className="text-end">{t('admin.col.failed')}</TableHead>
              <TableHead className="text-end">{t('admin.col.storage')}</TableHead>
              <TableHead>{t('admin.col.lastActive')}</TableHead>
              <TableHead>{t('admin.col.created')}</TableHead>
              <TableHead className="w-12 pe-5">
                <span className="sr-only">{t('admin.col.actions')}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((u) => {
              const isSelf = u.id === me?.id
              return (
                <TableRow key={u.id} className={cn(!u.is_active && 'text-muted-foreground')}>
                  <TableCell className="ps-5 font-medium">
                    <span className="inline-flex items-center gap-2">
                      <Avatar email={u.email} url={u.avatar_url} size="xs" />
                      {u.email}
                      {isSelf ? <Badge variant="secondary">{t('common.you')}</Badge> : null}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={u.role === 'admin' ? 'border-primary/40 text-primary' : undefined}>
                      {t(u.role === 'admin' ? 'admin.roleAdminShort' : 'admin.roleUserShort')}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5">
                      <span className={cn('size-1.5 rounded-full', u.is_active ? 'bg-ok' : 'bg-faint')} />
                      {t(u.is_active ? 'admin.active' : 'admin.disabledStatus')}
                    </span>
                  </TableCell>
                  <TableCell className="text-end tabular-nums">{u.usage.done}</TableCell>
                  <TableCell className="text-end tabular-nums">{u.usage.queued + u.usage.running}</TableCell>
                  <TableCell className={cn('text-end tabular-nums', u.usage.failed > 0 && 'text-destructive')}>{u.usage.failed}</TableCell>
                  <TableCell className="text-end tabular-nums">{fmtBytes(u.usage.stored_bytes)}</TableCell>
                  <TableCell>
                    {u.usage.last_job_at ? <time title={fmtWhen(u.usage.last_job_at)}>{fmtRelative(u.usage.last_job_at)}</time> : t('common.never')}
                  </TableCell>
                  <TableCell>
                    <time title={fmtWhen(u.created_at)}>{fmtRelative(u.created_at)}</time>
                  </TableCell>
                  <TableCell className="pe-5">
                    {/* Not modal: a modal menu that opens a dialog can leave the page unclickable. */}
                    <DropdownMenu modal={false}>
                      <DropdownMenuTrigger asChild>
                        <Button size="icon" variant="ghost" className="size-8" aria-label={t('admin.actionsFor', { email: u.email })}>
                          <MoreHorizontal className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-60">
                        <DropdownMenuItem onSelect={() => setResetFor(u)}>
                          <KeyRound /> {t('admin.resetPassword')}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        {isSelf ? (
                          <DropdownMenuItem disabled className="text-xs">
                            {t('admin.selfNote')}
                          </DropdownMenuItem>
                        ) : (
                          <>
                            {u.role === 'admin' ? (
                              <DropdownMenuItem onSelect={() => changeRole(u, 'user')}>
                                <ShieldOff /> {t('admin.makeUserItem')}
                              </DropdownMenuItem>
                            ) : (
                              <DropdownMenuItem onSelect={() => changeRole(u, 'admin')}>
                                <Shield /> {t('admin.makeAdminItem')}
                              </DropdownMenuItem>
                            )}
                            {u.is_active ? (
                              <DropdownMenuItem variant="destructive" onSelect={() => changeActive(u, false)}>
                                <UserX /> {t('admin.disableItem')}
                              </DropdownMenuItem>
                            ) : (
                              <DropdownMenuItem onSelect={() => changeActive(u, true)}>
                                <UserCheck /> {t('admin.enable')}
                              </DropdownMenuItem>
                            )}
                          </>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
      {creating ? <CreateUserDialog onClose={() => setCreating(false)} /> : null}
      {resetFor ? (
        <ResetPasswordDialog key={resetFor.id} user={resetFor} isSelf={resetFor.id === me?.id} onClose={() => setResetFor(null)} />
      ) : null}
    </Panel>
  )
}
