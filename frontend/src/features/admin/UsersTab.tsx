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
  return (
    <Field label="Password" error={error} hint={`At least ${MIN_PASSWORD} characters. You'll see it once to share.`}>
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
            <Wand2 /> Generate
          </Button>
        </div>
      )}
    </Field>
  )
}

function ResetPasswordDialog({ user, isSelf, onClose }: { user: AdminUser; isSelf: boolean; onClose: () => void }) {
  const update = useUpdateUser()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (password.length < MIN_PASSWORD) {
      setError(`Use at least ${MIN_PASSWORD} characters.`)
      return
    }
    update.mutate(
      { id: user.id, password },
      {
        onSuccess: () => {
          setDone(true)
          toast.success(`Password changed for ${user.email}`)
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{done ? 'Password changed' : 'Reset password'}</DialogTitle>
          <DialogDescription>
            {done
              ? isSelf
                ? 'You stay signed in here. Your other devices will ask for the new password.'
                : `Share it with ${user.email} now - it won't be shown again. They've been signed out everywhere.`
              : isSelf
                ? 'Set a new password for your own account.'
                : `Set a new password for ${user.email}. They'll be signed out everywhere.`}
          </DialogDescription>
        </DialogHeader>
        {done ? (
          <>
            <SecretReveal value={password} />
            <DialogFooter>
              <Button onClick={onClose}>Done</Button>
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
                Cancel
              </Button>
              <Button type="submit" disabled={update.isPending}>
                {update.isPending ? <Loader2 className="animate-spin" /> : null}
                Set password
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
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
      ...(EMAIL.test(address) ? {} : { email: "That doesn't look like an email address." }),
      ...(password.length >= MIN_PASSWORD ? {} : { password: `Use at least ${MIN_PASSWORD} characters.` }),
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

  const details = created ? `Email: ${created}\nPassword: ${password}\nSign in at ${window.location.origin}/login` : ''

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{created ? 'Account created' : 'Create a user'}</DialogTitle>
          <DialogDescription>
            {created
              ? "Send these sign-in details now - the password won't be shown again."
              : 'H3 Studio is invite-only: accounts exist only when an admin makes them.'}
          </DialogDescription>
        </DialogHeader>
        {created ? (
          <>
            <SecretReveal value={details} copyText={details} label="Copy all" />
            <DialogFooter>
              <Button onClick={onClose}>Done</Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="grid gap-5" noValidate>
            <Field label="Email" error={errors.email}>
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
            <Field label="Role">
              {(props) => (
                <Select value={role} onValueChange={(v) => setRole(v as Role)}>
                  <SelectTrigger id={props.id} className="h-9 w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">User · makes clips</SelectItem>
                    <SelectItem value="admin">Admin · also manages the GPU and accounts</SelectItem>
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
                Cancel
              </Button>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? <Loader2 className="animate-spin" /> : null}
                Create user
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
  const update = useUpdateUser()
  const confirm = useConfirm()
  const [creating, setCreating] = useState(false)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)

  const changeRole = (u: AdminUser, role: Role) =>
    void confirm({
      title: role === 'admin' ? `Make ${u.email} an admin?` : `Make ${u.email} a regular user?`,
      description:
        role === 'admin'
          ? 'Admins manage every account, change the GPU policy and budget, and see what everyone has spent.'
          : 'They keep their clips but lose the Admin page.',
      confirmLabel: role === 'admin' ? 'Make admin' : 'Make regular user',
      action: () => update.mutateAsync({ id: u.id, role }).then(() => void toast.success(`${u.email} is now ${role === 'admin' ? 'an admin' : 'a regular user'}`)),
    })

  const changeActive = (u: AdminUser, active: boolean) => {
    if (active) {
      update.mutate(
        { id: u.id, is_active: true },
        { onSuccess: () => toast.success(`${u.email} can sign in again`), onError: (err) => toast.error(errorMessage(err)) },
      )
      return
    }
    void confirm({
      title: `Disable ${u.email}?`,
      description: "They're signed out now and can't sign in until you enable the account again. Their clips are kept.",
      confirmLabel: 'Disable',
      destructive: true,
      action: () => update.mutateAsync({ id: u.id, is_active: false }).then(() => void toast.success(`${u.email} is disabled`)),
    })
  }

  const list = users.data?.users ?? []

  return (
    <Panel
      title="Users"
      description={users.data ? `${list.length} ${list.length === 1 ? 'account' : 'accounts'}. Each person sees only their own clips.` : undefined}
      actions={
        <Button onClick={() => setCreating(true)} className="shrink-0">
          <UserPlus /> Create user
        </Button>
      }
      className="p-0 [&>header]:p-5 [&>header]:pb-0"
    >
      {users.isPending ? (
        <div className="grid gap-2 p-5" aria-label="Loading users">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : !users.data ? (
        <EmptyState
          icon={WifiOff}
          title="Couldn't load the users"
          action={
            <Button size="sm" variant="outline" onClick={() => void users.refetch()}>
              Try again
            </Button>
          }
        />
      ) : (
        <Table className="text-sm">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-5">Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Clips</TableHead>
              <TableHead className="text-right">In queue</TableHead>
              <TableHead className="text-right">Failed</TableHead>
              <TableHead className="text-right">Storage</TableHead>
              <TableHead>Last active</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="w-12 pr-5">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((u) => {
              const isSelf = u.id === me?.id
              return (
                <TableRow key={u.id} className={cn(!u.is_active && 'text-muted-foreground')}>
                  <TableCell className="pl-5 font-medium">
                    <span className="inline-flex items-center gap-2">
                      <Avatar email={u.email} url={u.avatar_url} size="xs" />
                      {u.email}
                      {isSelf ? <Badge variant="secondary">You</Badge> : null}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={u.role === 'admin' ? 'border-primary/40 text-primary' : undefined}>
                      {u.role === 'admin' ? 'Admin' : 'User'}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5">
                      <span className={cn('size-1.5 rounded-full', u.is_active ? 'bg-ok' : 'bg-faint')} />
                      {u.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{u.usage.done}</TableCell>
                  <TableCell className="text-right tabular-nums">{u.usage.queued + u.usage.running}</TableCell>
                  <TableCell className={cn('text-right tabular-nums', u.usage.failed > 0 && 'text-destructive')}>{u.usage.failed}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmtBytes(u.usage.stored_bytes)}</TableCell>
                  <TableCell>
                    {u.usage.last_job_at ? <time title={fmtWhen(u.usage.last_job_at)}>{fmtRelative(u.usage.last_job_at)}</time> : 'Never'}
                  </TableCell>
                  <TableCell>
                    <time title={fmtWhen(u.created_at)}>{fmtRelative(u.created_at)}</time>
                  </TableCell>
                  <TableCell className="pr-5">
                    {/* Not modal: a modal menu that opens a dialog can leave the page unclickable. */}
                    <DropdownMenu modal={false}>
                      <DropdownMenuTrigger asChild>
                        <Button size="icon" variant="ghost" className="size-8" aria-label={`Actions for ${u.email}`}>
                          <MoreHorizontal className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-60">
                        <DropdownMenuItem onSelect={() => setResetFor(u)}>
                          <KeyRound /> Reset password…
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        {isSelf ? (
                          <DropdownMenuItem disabled className="text-xs">
                            You can't demote or disable yourself.
                          </DropdownMenuItem>
                        ) : (
                          <>
                            {u.role === 'admin' ? (
                              <DropdownMenuItem onSelect={() => changeRole(u, 'user')}>
                                <ShieldOff /> Make regular user…
                              </DropdownMenuItem>
                            ) : (
                              <DropdownMenuItem onSelect={() => changeRole(u, 'admin')}>
                                <Shield /> Make admin…
                              </DropdownMenuItem>
                            )}
                            {u.is_active ? (
                              <DropdownMenuItem variant="destructive" onSelect={() => changeActive(u, false)}>
                                <UserX /> Disable…
                              </DropdownMenuItem>
                            ) : (
                              <DropdownMenuItem onSelect={() => changeActive(u, true)}>
                                <UserCheck /> Enable
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
